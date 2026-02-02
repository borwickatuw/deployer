#!/usr/bin/env python3
"""
Deployment speed test harness.

Measures end-to-end deployment time including time until changes are visible
at the target URL. Uses a unique marker injected into the app's health check
to detect when the new deployment is live.

Usage:
    # Basic usage (matches deploy.py arguments)
    python bin/deployment-speed-test.py /path/to/deploy.toml myapp-staging

    # With output files
    python bin/deployment-speed-test.py /path/to/deploy.toml myapp-staging \
        --output results/test-001.json \
        --csv results/summary.csv

Prerequisites:
    - The target app must have BUILD_MARKER support in its health check
    - Cognito test credentials must be stored in SSM (for protected environments)
"""

import argparse
import json
import secrets
import subprocess
import sys
import threading
import time
from pathlib import Path

try:
    import tomllib  # Python 3.11+
except ImportError:
    import tomli as tomllib  # Fallback for older Python

import boto3
import requests

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from deployer.core import (
    get_staging_url_from_config,
    load_environment_config,
)
from deployer.timing import DeploymentTimer
from deployer.utils import Colors, configure_aws_profile, get_environment_path, log, log_error, log_success, log_warning


class CognitoAuthenticator:
    """Handles Cognito authentication for accessing protected staging environments."""

    def __init__(self, environment: str, region: str = "us-west-2"):
        self.environment = environment
        self.region = region
        self.ssm = boto3.client("ssm", region_name=region)
        self.cognito = boto3.client("cognito-idp", region_name=region)
        self._tokens = None
        self._config = None

    def _load_config(self) -> dict:
        """Load environment config from config.toml."""
        if self._config is None:
            env_dir = get_environment_path(self.environment)
            if not env_dir.exists():
                raise RuntimeError(f"Environment directory not found: {env_dir}")
            self._config = load_environment_config(env_dir)
        return self._config

    def get_password(self) -> str:
        """Retrieve the deployer test password from SSM."""
        config = self._load_config()
        cognito_config = config.get("cognito", {})
        param_name = cognito_config.get("test_password_ssm")

        if not param_name:
            # Fall back to conventional path
            param_name = f"/deployer/{self.environment}/cognito-test-password"

        try:
            response = self.ssm.get_parameter(Name=param_name, WithDecryption=True)
            return response["Parameter"]["Value"]
        except self.ssm.exceptions.ParameterNotFound:
            raise RuntimeError(
                f"Cognito test password not found in SSM at {param_name}. "
                "See docs/STAGING-ENVIRONMENTS.md for setup instructions."
            )

    def get_user_pool_info(self) -> tuple[str, str, str]:
        """Get user pool ID, client ID, and client secret from config.toml.

        Client secret is fetched from tofu outputs since it's sensitive.
        """
        config = self._load_config()
        cognito_config = config.get("cognito", {})

        user_pool_id = cognito_config.get("user_pool_id")
        client_id = cognito_config.get("client_id")

        if not user_pool_id:
            raise RuntimeError(f"cognito.user_pool_id not found in config for {self.environment}")
        if not client_id:
            raise RuntimeError(f"cognito.client_id not found in config for {self.environment}")

        # Get client secret from tofu outputs (not stored in config.toml since it's sensitive)
        env_dir = get_environment_path(self.environment)
        result = subprocess.run(
            ["tofu", "output", "-raw", "cognito_user_pool_client_secret"],
            cwd=env_dir,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise RuntimeError(f"Failed to get cognito_user_pool_client_secret: {result.stderr}")
        client_secret = result.stdout.strip()

        return user_pool_id, client_id, client_secret

    def _compute_secret_hash(self, username: str, client_id: str, client_secret: str) -> str:
        """Compute the SECRET_HASH for Cognito authentication."""
        import base64
        import hashlib
        import hmac

        message = username + client_id
        dig = hmac.new(
            client_secret.encode("utf-8"),
            message.encode("utf-8"),
            digestmod=hashlib.sha256,
        ).digest()
        return base64.b64encode(dig).decode()

    def authenticate(self) -> dict:
        """Authenticate with Cognito and return tokens."""
        if self._tokens:
            return self._tokens

        config = self._load_config()
        cognito_config = config.get("cognito", {})
        username = cognito_config.get("test_username", "deployer@test.local")
        password = self.get_password()
        user_pool_id, client_id, client_secret = self.get_user_pool_info()

        # Compute SECRET_HASH required by the client
        secret_hash = self._compute_secret_hash(username, client_id, client_secret)

        try:
            response = self.cognito.admin_initiate_auth(
                UserPoolId=user_pool_id,
                ClientId=client_id,
                AuthFlow="ADMIN_USER_PASSWORD_AUTH",
                AuthParameters={
                    "USERNAME": username,
                    "PASSWORD": password,
                    "SECRET_HASH": secret_hash,
                },
            )

            if "ChallengeName" in response:
                raise RuntimeError(
                    f"Authentication requires challenge: {response['ChallengeName']}. "
                    "The deployer test account may need password reset."
                )

            self._tokens = response["AuthenticationResult"]
            return self._tokens

        except self.cognito.exceptions.NotAuthorizedException:
            raise RuntimeError(
                f"Authentication failed. Check that the {username} account "
                "exists and the password in SSM is correct."
            )

    def get_session(self, target_url: str) -> requests.Session:
        """Create an authenticated requests session by simulating browser OAuth flow.

        Args:
            target_url: The protected URL to authenticate against (e.g., https://site/health/)

        Returns:
            requests.Session with ALB session cookie set.
        """
        import html as html_module
        import re
        from urllib.parse import urlparse

        username = "deployer@test.local"
        password = self.get_password()

        session = requests.Session()

        # Step 1: Request the protected URL - this will redirect to Cognito
        resp = session.get(target_url, allow_redirects=True, timeout=30)

        # We should now be at the Cognito login page
        if "cognito" not in resp.url.lower():
            # Maybe already authenticated or no auth required
            if resp.status_code == 200:
                return session
            raise RuntimeError(f"Unexpected response: {resp.status_code} at {resp.url}")

        # Step 2: Parse the login form
        # The Cognito hosted UI has a form with csrf token and other hidden fields
        page_html = resp.text

        # Extract the form action URL (decode HTML entities like &amp;)
        form_match = re.search(r'<form[^>]+action="([^"]+)"', page_html)
        if not form_match:
            raise RuntimeError("Could not find login form in Cognito page")
        form_action = html_module.unescape(form_match.group(1))

        # Extract hidden fields (csrf token, etc.) - decode HTML entities in values
        hidden_fields = {}
        for match in re.finditer(r'<input[^>]+type="hidden"[^>]*name="([^"]+)"[^>]*value="([^"]*)"', page_html):
            hidden_fields[match.group(1)] = html_module.unescape(match.group(2))
        for match in re.finditer(r'<input[^>]+name="([^"]+)"[^>]*type="hidden"[^>]*value="([^"]*)"', page_html):
            hidden_fields[match.group(1)] = html_module.unescape(match.group(2))

        # Step 3: Submit the login form
        login_data = {
            **hidden_fields,
            "username": username,
            "password": password,
        }

        # The form action might be relative
        if form_action.startswith("/"):
            parsed = urlparse(resp.url)
            form_url = f"{parsed.scheme}://{parsed.netloc}{form_action}"
        else:
            form_url = form_action

        # Submit the form - this should redirect back to the ALB
        resp = session.post(
            form_url,
            data=login_data,
            allow_redirects=True,
            timeout=30,
        )

        # Step 4: Verify we got back to the original site with a session cookie
        if "AWSELBAuthSessionCookie" not in str(session.cookies):
            # Check if we got an error page
            if "error" in resp.url.lower() or resp.status_code >= 400:
                raise RuntimeError(f"Login failed. Response URL: {resp.url}, Status: {resp.status_code}")
            # Maybe the cookie has a different name or login succeeded differently
            log_warning(f"No AWSELBAuthSessionCookie found, but got status {resp.status_code}")

        return session


class MarkerPoller:
    """Polls a URL until a specific marker appears in the response."""

    def __init__(
        self,
        url: str,
        marker: str,
        session: requests.Session | None = None,
        poll_interval: float = 2.0,
        timeout: float = 600.0,
    ):
        self.url = url
        self.marker = marker
        self.session = session or requests.Session()
        self.poll_interval = poll_interval
        self.timeout = timeout

        self._found_time: float | None = None
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._error: str | None = None

    def start(self) -> None:
        """Start polling in a background thread."""
        self._thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Stop polling."""
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=5.0)

    def wait(self, timeout: float | None = None) -> float | None:
        """Wait for the marker to be found.

        Args:
            timeout: Maximum time to wait in seconds.

        Returns:
            Timestamp when marker was found, or None if not found.
        """
        if self._thread:
            self._thread.join(timeout=timeout or self.timeout)
        return self._found_time

    @property
    def found_time(self) -> float | None:
        """Timestamp when the marker was found."""
        return self._found_time

    @property
    def error(self) -> str | None:
        """Error message if polling failed."""
        return self._error

    def _poll_loop(self) -> None:
        """Polling loop running in background thread."""
        start_time = time.time()

        while not self._stop_event.is_set():
            elapsed = time.time() - start_time
            if elapsed > self.timeout:
                self._error = f"Timeout after {self.timeout}s waiting for marker"
                return

            try:
                response = self.session.get(self.url, timeout=10)

                # Check for redirect (Cognito auth required)
                if response.status_code == 302:
                    # This means auth isn't working correctly
                    log_warning(f"Got 302 redirect - auth may not be configured correctly")

                elif response.status_code == 200:
                    try:
                        data = response.json()
                        if data.get("build_marker") == self.marker:
                            self._found_time = time.time()
                            return
                    except json.JSONDecodeError:
                        pass  # Response wasn't JSON, keep polling

            except requests.RequestException as e:
                # Log but continue polling
                log_warning(f"Poll request failed: {e}")

            time.sleep(self.poll_interval)


def load_deploy_toml(path: Path) -> dict:
    """Load and parse deploy.toml file."""
    with open(path, "rb") as f:
        return tomllib.load(f)


def get_health_check_path(deploy_config: dict) -> str:
    """Get health check path from deploy.toml.

    Looks for health_check_path in [services.web] section.
    Falls back to "/health/" if not found.
    """
    services = deploy_config.get("services", {})
    web_service = services.get("web", {})
    return web_service.get("health_check_path", "/health/")


def get_app_name(deploy_config: dict) -> str:
    """Get application name from deploy.toml."""
    application = deploy_config.get("application", {})
    return application.get("name", "unknown")


def inject_marker(deploy_toml_path: Path, marker: str) -> str:
    """Inject a BUILD_MARKER into deploy.toml.

    Args:
        deploy_toml_path: Path to deploy.toml file.
        marker: The marker string to inject.

    Returns:
        The original content for restoration.
    """
    original_content = deploy_toml_path.read_text()

    # Read file as text and inject/update BUILD_MARKER
    # The marker must be added immediately after [environment] line,
    # NOT after [environment.staging] or other subsections.
    lines = original_content.split("\n")
    new_lines = []
    marker_added = False

    for i, line in enumerate(lines):
        # If this line is an existing BUILD_MARKER, replace it
        if line.strip().startswith("BUILD_MARKER"):
            new_lines.append(f'BUILD_MARKER = "{marker}"')
            marker_added = True
            continue

        new_lines.append(line)

        # Add marker immediately after [environment] line (not subsections)
        if line.strip() == "[environment]" and not marker_added:
            new_lines.append(f'BUILD_MARKER = "{marker}"')
            marker_added = True

    deploy_toml_path.write_text("\n".join(new_lines))
    return original_content


def restore_toml(deploy_toml_path: Path, original_content: str) -> None:
    """Restore deploy.toml to its original content."""
    deploy_toml_path.write_text(original_content)


def run_deployment(
    config_path: Path,
    environment: str,
    timer: DeploymentTimer,
    dry_run: bool = False,
) -> None:
    """Run the deployment with timing.

    Args:
        config_path: Path to deploy.toml.
        environment: Target environment (e.g., myapp-staging).
        timer: DeploymentTimer instance.
        dry_run: If True, skip actual deployment.
    """
    # Run deployment as subprocess
    script_path = Path(__file__).parent / "deploy.py"

    cmd = [
        sys.executable,
        str(script_path),
        str(config_path),
        environment,
        "--timing",
        "--run-id", timer.report.run_id,
    ]

    if dry_run:
        cmd.append("--dry-run")

    # Run deployment and capture timing output
    result = subprocess.run(cmd, capture_output=False)

    if result.returncode != 0:
        raise RuntimeError(f"Deployment failed with exit code {result.returncode}")


def main():
    # Load .env and configure AWS profile
    configure_aws_profile("deploy")

    parser = argparse.ArgumentParser(
        description="Deployment speed test harness",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s /path/to/deploy.toml myapp-staging
  %(prog)s /path/to/deploy.toml myapp-production --output results/test.json
  %(prog)s /path/to/deploy.toml myapp-staging --csv results/summary.csv
  %(prog)s /path/to/deploy.toml myapp-staging --dry-run
"""
    )
    parser.add_argument(
        "config",
        type=Path,
        help="Path to the application's deploy.toml file"
    )
    parser.add_argument(
        "environment",
        help="Environment name (e.g., myapp-staging, myapp-production)"
    )
    parser.add_argument(
        "--output", "-o",
        metavar="FILE",
        help="Save detailed timing report to JSON file"
    )
    parser.add_argument(
        "--csv",
        metavar="FILE",
        help="Append timing summary to CSV file"
    )
    parser.add_argument(
        "--marker",
        help="Use specific marker (auto-generated if not specified)"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Skip actual deployment (for testing the harness)"
    )
    parser.add_argument(
        "--skip-auth",
        action="store_true",
        help="Skip Cognito authentication (for non-protected environments)"
    )
    parser.add_argument(
        "--poll-interval",
        type=float,
        default=2.0,
        help="Seconds between health check polls (default: 2.0)"
    )

    args = parser.parse_args()

    # Validate config file exists
    if not args.config.exists():
        log_error(f"Config file not found: {args.config}")
        sys.exit(1)

    # Load deploy.toml to get app info
    deploy_config = load_deploy_toml(args.config)
    app_name = get_app_name(deploy_config)
    health_path = get_health_check_path(deploy_config)

    # Load environment config to get the staging URL
    env_dir = get_environment_path(args.environment)
    if not env_dir.exists():
        log_error(f"Environment directory not found: {env_dir}")
        sys.exit(1)

    try:
        env_config = load_environment_config(env_dir)
        staging_url = get_staging_url_from_config(env_config)
        if not staging_url:
            log_error(f"Unable to determine staging URL from config for {args.environment}")
            sys.exit(1)
    except (FileNotFoundError, RuntimeError) as e:
        log_error(f"Failed to load environment config: {e}")
        sys.exit(1)

    # Generate marker
    marker = args.marker or f"speedtest-{secrets.token_hex(4)}"
    run_id = f"speedtest-{marker.split('-')[-1]}"

    print()
    print(f"{Colors.BLUE}Deployment Speed Test{Colors.NC}")
    print(f"  App:         {app_name}")
    print(f"  Config:      {args.config}")
    print(f"  Environment: {args.environment}")
    print(f"  Marker:      {marker}")
    print(f"  URL:         {staging_url}{health_path}")
    print()

    # Initialize timer
    timer = DeploymentTimer(run_id)

    # Set up authenticated session for polling
    health_url = f"{staging_url}{health_path}"
    session = None
    if not args.skip_auth:
        try:
            log("Authenticating with Cognito...")
            auth = CognitoAuthenticator(args.environment)
            session = auth.get_session(health_url)
            log_success("Authenticated")
        except Exception as e:
            log_error(f"Authentication failed: {e}")
            log_warning("Continuing without authentication (may get 302 redirects)")
            session = requests.Session()
    else:
        session = requests.Session()

    # Inject marker into deploy.toml
    log(f"Injecting marker into {args.config}...")
    original_content = inject_marker(args.config, marker)
    log_success(f"Marker injected: {marker}")

    try:
        # Record start time for overall measurement
        deploy_start = time.time()

        # Start polling for marker immediately
        poller = MarkerPoller(
            url=health_url,
            marker=marker,
            session=session,
            poll_interval=args.poll_interval,
        )

        log("Starting deployment and visibility polling...")
        poller.start()

        # Run deployment
        try:
            run_deployment(args.config, args.environment, timer, args.dry_run)
        except Exception as e:
            log_error(f"Deployment failed: {e}")
            poller.stop()
            raise

        # Record time to stable
        time_to_stable = time.time() - deploy_start
        timer.set_time_to_stable(time_to_stable)

        # Wait for marker if not found yet (should already be found by now)
        if poller.found_time is None:
            log("Waiting for marker to appear...")
            poller.wait(timeout=120)  # Additional wait after deployment

        poller.stop()

        # Record time to visible
        if poller.found_time:
            time_to_visible = poller.found_time - deploy_start
            timer.set_time_to_visible(time_to_visible)

        # Print results
        print()
        print(f"{Colors.GREEN}Speed Test Results{Colors.NC}")
        print(f"  Run ID:           {run_id}")

        if poller.found_time:
            print(f"  Time to visible:  {time_to_visible:.2f}s")
            print(f"  Time to stable:   {time_to_stable:.2f}s")
            gap = time_to_stable - time_to_visible
            print(f"  Visibility gap:   {gap:.2f}s")
        else:
            print(f"  Time to visible:  NOT DETECTED")
            print(f"  Time to stable:   {time_to_stable:.2f}s")
            if poller.error:
                print(f"  Error:            {poller.error}")

        # Print step breakdown
        print()
        print("Step breakdown:")
        for step in timer.report.steps:
            print(f"  {step.name}: {step.duration_seconds:.2f}s")
            for sub in step.sub_steps:
                print(f"    {sub.name}: {sub.duration_seconds:.2f}s")

        # Save outputs
        if args.output:
            output_path = Path(args.output)
            timer.report.save_json(output_path)
            print()
            log_success(f"Report saved to {output_path}")

        if args.csv:
            csv_path = Path(args.csv)
            timer.report.append_csv(csv_path)
            log_success(f"Summary appended to {csv_path}")

    finally:
        # Restore original deploy.toml
        log("Restoring deploy.toml...")
        restore_toml(args.config, original_content)
        log_success("deploy.toml restored")


if __name__ == "__main__":
    main()
