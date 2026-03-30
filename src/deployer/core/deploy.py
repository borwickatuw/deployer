"""Deployment logic for ECS applications."""

from collections import deque


def topological_sort(images: dict[str, dict]) -> list[str]:
    """Sort images by dependencies using Kahn's algorithm.

    Args:
        images: Dict of image_name -> image_config, where config may have
                'depends_on' list.

    Returns:
        List of image names in build order (dependencies first).

    Raises:
        ValueError: If there's a circular dependency or unknown dependency.
    """
    if not images:
        return []

    # Build adjacency list and in-degree count
    in_degree = {name: 0 for name in images}
    dependents = {name: [] for name in images}  # name -> list of images that depend on it

    for name, config in images.items():
        deps = config.get("depends_on", [])
        for dep in deps:
            if dep not in images:
                raise ValueError(f"Image '{name}' depends on unknown image '{dep}'")
            dependents[dep].append(name)
            in_degree[name] += 1

    # Start with images that have no dependencies
    queue = deque([name for name, degree in in_degree.items() if degree == 0])
    result = []

    while queue:
        current = queue.popleft()
        result.append(current)

        for dependent in dependents[current]:
            in_degree[dependent] -= 1
            if in_degree[dependent] == 0:
                queue.append(dependent)

    if len(result) != len(images):
        # Find the cycle for a helpful error message
        remaining = [name for name in images if name not in result]
        raise ValueError(f"Circular dependency detected among images: {remaining}")

    return result
