import numpy as np
from scipy import optimize


def fit_ellipse(x, y):
    """Fit an ellipse to the given set of points.

    Args:
        x (np.array): x-coordinates of the points
        y (np.array): y-coordinates of the points

    Returns:
        np.array: Parameters of the fitted ellipse (xc, yc, a, b, theta)

    """

    def f(c):
        xc, yc, a, b, theta = c
        distance = (
            ((x - xc) * np.cos(theta) + (y - yc) * np.sin(theta)) ** 2 / a**2
            + ((x - xc) * np.sin(theta) - (y - yc) * np.cos(theta)) ** 2 / b**2
            - 1
        )
        return distance

    x_m, y_m = np.mean(x), np.mean(y)
    center_estimate = x_m, y_m
    a_estimate = np.max(np.abs(x - x_m))
    b_estimate = np.max(np.abs(y - y_m))
    theta_estimate = 0

    estimate = [*center_estimate, a_estimate, b_estimate, theta_estimate]
    result = optimize.minimize(
        lambda c: np.sum(f(c) ** 2),
        estimate,
        method="SLSQP",
        constraints={"type": "ineq", "fun": lambda c: c[2] - c[3]},
    )

    return result.x


def find_closest_point(points, pos, factor):
    """Find the closest point to the given position.

    Args:
        points (list): List of QPointF objects representing the points.
        pos (QPointF): The position to check.
        factor (float): The zoom factor.

    Returns:
        QPointF or None: The closest point if within the threshold, otherwise None.

    """
    min_dist = float("inf")
    closest_point = None

    for point in points:
        dist = (point.x() - pos.x()) ** 2 + (point.y() - pos.y()) ** 2
        if dist < min_dist:
            min_dist = dist
            closest_point = point

    # Only select the point if it's within a certain radius
    if min_dist < (10 / factor) ** 2:
        return closest_point
    return None
