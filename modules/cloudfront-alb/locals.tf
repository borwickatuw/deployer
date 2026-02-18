# ------------------------------------------------------------------------------
# Locals
# ------------------------------------------------------------------------------

locals {
  error_page_html = var.error_page_content != null ? var.error_page_content : <<-EOF
    <!DOCTYPE html>
    <html lang="en">
    <head>
      <meta charset="UTF-8">
      <meta name="viewport" content="width=device-width, initial-scale=1.0">
      <title>${var.name_prefix} - Service Temporarily Unavailable</title>
      <style>
        * {
          margin: 0;
          padding: 0;
          box-sizing: border-box;
        }
        body {
          font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;
          background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
          min-height: 100vh;
          display: flex;
          align-items: center;
          justify-content: center;
          padding: 20px;
        }
        .container {
          background: white;
          border-radius: 12px;
          padding: 48px;
          max-width: 500px;
          text-align: center;
          box-shadow: 0 25px 50px -12px rgba(0, 0, 0, 0.25);
        }
        .icon {
          font-size: 64px;
          margin-bottom: 24px;
        }
        h1 {
          color: #1a202c;
          font-size: 24px;
          margin-bottom: 16px;
        }
        p {
          color: #4a5568;
          font-size: 16px;
          line-height: 1.6;
          margin-bottom: 24px;
        }
        ul {
          text-align: left;
          display: inline-block;
          margin: 0 0 24px 0;
          color: #4a5568;
        }
        .env-badge {
          display: inline-block;
          background: #e2e8f0;
          color: #4a5568;
          padding: 4px 12px;
          border-radius: 9999px;
          font-size: 13px;
          font-weight: 500;
          margin-bottom: 24px;
        }
      </style>
    </head>
    <body>
      <main class="container" role="main">
        <div class="icon" aria-hidden="true">&#9888;&#65039;</div>
        <div class="env-badge">${var.name_prefix}</div>
        <h1>Service Temporarily Unavailable</h1>
        <p>
          This staging environment is currently unavailable. Possible reasons:
        </p>
        <ul>
          <li>Scheduled downtime outside business hours (Pacific Time)</li>
          <li>Deployment or maintenance in progress</li>
        </ul>
        <p>
          Please try again later or contact the development team if this is unexpected.
        </p>
      </main>
    </body>
    </html>
  EOF
}
