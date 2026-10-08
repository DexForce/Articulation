from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import TypedDict, Unpack

import numpy as np
from scipy.interpolate import BPoly, CubicHermiteSpline  # pyright: ignore[reportMissingTypeStubs]
from scipy.spatial.transform import Rotation  # pyright: ignore[reportMissingTypeStubs]

from articraft.sdk._mesh.core import (
    _EPS,
    LoftGeometry,
    MeshGeometry,
    Vec2,
    Vec3,
    _v_add,
    _v_norm,
    _v_sub,
    _vec3,
)


def _sample_cubic_bezier(
    control_points: Sequence[Sequence[float]], *, dimensions: int, samples_per_segment: int
) -> list[tuple[float, ...]]:
    points = [tuple(float(value) for value in point) for point in control_points]
    if any(len(point) != dimensions for point in points):
        raise ValueError(f"Bezier control points must have {dimensions} values")
    if len(points) < 4 or (len(points) - 1) % 3:
        raise ValueError("Bezier control points must contain 3n + 1 points")
    steps = max(2, int(samples_per_segment))
    segments = (len(points) - 1) // 3
    array = np.asarray(points, dtype=np.float64)
    # Bezier control points are Bernstein coefficients, which is BPoly's
    # native representation: coefficient k of segment s is control point 3s+k.
    spline = BPoly(
        np.stack([array[k : k + 3 * segments : 3] for k in range(4)]),
        np.arange(segments + 1, dtype=np.float64),
    )
    samples = np.arange(segments * steps, dtype=np.float64) / steps
    result = [tuple(float(value) for value in row) for row in spline(samples)]
    result.append(points[-1])
    return result


def sample_cubic_bezier_spline_2d(
    control_points: Sequence[Vec2], *, samples_per_segment: int = 12
) -> list[Vec2]:
    return [
        (point[0], point[1])
        for point in _sample_cubic_bezier(
            control_points, dimensions=2, samples_per_segment=samples_per_segment
        )
    ]


def sample_cubic_bezier_spline_3d(
    control_points: Sequence[Vec3], *, samples_per_segment: int = 12
) -> list[Vec3]:
    return [
        (point[0], point[1], point[2])
        for point in _sample_cubic_bezier(
            control_points, dimensions=3, samples_per_segment=samples_per_segment
        )
    ]


def _tuple_distance(a: Sequence[float], b: Sequence[float]) -> float:
    return math.sqrt(sum((left - right) ** 2 for left, right in zip(a, b, strict=True)))


def _sample_catmull_rom(
    points: Sequence[Sequence[float]],
    *,
    dimensions: int,
    samples_per_segment: int,
    closed: bool,
    alpha: float,
) -> list[tuple[float, ...]]:
    if not 0.0 <= alpha <= 1.0:
        raise ValueError("Catmull-Rom alpha must be between 0 and 1")
    raw = [tuple(float(value) for value in point) for point in points]
    if any(len(point) != dimensions for point in raw):
        raise ValueError(f"Catmull-Rom points must have {dimensions} values")
    deduplicated = [raw[0]] if raw else []
    for point in raw[1:]:
        if _tuple_distance(point, deduplicated[-1]) > _EPS:
            deduplicated.append(point)
    if len(deduplicated) < 2:
        raise ValueError("Catmull-Rom spline requires at least two distinct points")
    steps = max(2, int(samples_per_segment))
    if closed:
        if _tuple_distance(deduplicated[0], deduplicated[-1]) <= _EPS:
            deduplicated.pop()
        if len(deduplicated) < 3:
            raise ValueError("closed Catmull-Rom spline requires at least three points")
        core = np.asarray(deduplicated, dtype=np.float64)
        # Wrap-around context: one phantom point behind, two ahead, so every
        # real segment has the neighbors the tangent formula reads.
        extended = np.vstack([core[-1], core, core[:2]])
        segment_count = len(core)
    else:
        core = np.asarray(deduplicated, dtype=np.float64)
        if len(core) == 2:
            amounts = np.arange(steps + 1, dtype=np.float64)[:, None] / steps
            values = core[0] + (core[1] - core[0]) * amounts
            return [tuple(float(value) for value in row) for row in values]
        # Reflected phantom endpoints keep the authored end tangents.
        extended = np.vstack([2.0 * core[0] - core[1], core, 2.0 * core[-1] - core[-2]])
        segment_count = len(core) - 1

    gaps = np.maximum(np.linalg.norm(np.diff(extended, axis=0), axis=1), _EPS) ** alpha
    knots = np.concatenate([[0.0], np.cumsum(gaps)])
    # The Barry-Goldman pyramid on knots (t0..t3) is the unique cubic through
    # P1 and P2 with these endpoint derivatives, so a Hermite spline with the
    # non-uniform Catmull-Rom tangents reproduces it exactly.
    spans = np.diff(extended, axis=0) / np.diff(knots)[:, None]
    central = (extended[2:] - extended[:-2]) / (knots[2:] - knots[:-2])[:, None]
    tangents = spans[:-1] + spans[1:] - central
    spline = CubicHermiteSpline(knots[1:-1], extended[1:-1], tangents)
    starts = knots[1 : 1 + segment_count, None]
    ends = knots[2 : 2 + segment_count, None]
    samples = (starts + (ends - starts) * np.arange(steps) / steps).reshape(-1)
    result = [tuple(float(value) for value in row) for row in spline(samples)]
    result.append(result[0] if closed else deduplicated[-1])
    return result


def sample_catmull_rom_spline_2d(
    points: Sequence[Vec2],
    *,
    samples_per_segment: int = 12,
    closed: bool = False,
    alpha: float = 0.5,
) -> list[Vec2]:
    return [
        (point[0], point[1])
        for point in _sample_catmull_rom(
            points,
            dimensions=2,
            samples_per_segment=samples_per_segment,
            closed=closed,
            alpha=float(alpha),
        )
    ]


def sample_catmull_rom_spline_3d(
    points: Sequence[Vec3],
    *,
    samples_per_segment: int = 12,
    closed: bool = False,
    alpha: float = 0.5,
) -> list[Vec3]:
    return [
        (point[0], point[1], point[2])
        for point in _sample_catmull_rom(
            points,
            dimensions=3,
            samples_per_segment=samples_per_segment,
            closed=closed,
            alpha=float(alpha),
        )
    ]


def sample_arc_3d(
    *,
    start_point: Vec3,
    center: Vec3,
    normal: Vec3,
    angle: float,
    segments: int = 16,
) -> list[Vec3]:
    origin = np.asarray(center, dtype=np.float64)
    axis = np.asarray(normal, dtype=np.float64)
    length = float(np.linalg.norm(axis))
    if length <= _EPS:
        raise ValueError("arc normal must be non-zero")
    axis /= length
    relative = np.asarray(start_point, dtype=np.float64) - origin
    radius = float(np.linalg.norm(relative))
    if radius <= _EPS:
        raise ValueError("arc start_point must differ from center")
    if abs(float(relative @ axis)) / radius > 1.0 - 1e-6:
        raise ValueError("arc radius must not be collinear with its normal")
    count = max(2, int(segments))
    angles = float(angle) * np.arange(count + 1, dtype=np.float64) / count
    points = origin + Rotation.from_rotvec(np.outer(angles, axis)).apply(relative)
    return [(float(x), float(y), float(z)) for x, y, z in points]


@dataclass
class WirePath:
    points: list[Vec3]

    def __init__(self, start: Vec3):
        self.points = [_vec3(start, name="start")]

    @classmethod
    def from_points(cls, points: Iterable[Vec3]) -> WirePath:
        values = list(points)
        if not values:
            raise ValueError("WirePath.from_points requires at least one point")
        return cls(values[0]).extend(values[1:])

    def _append(self, point: Vec3) -> None:
        value = _vec3(point, name="point")
        if _v_norm(_v_sub(value, self.points[-1])) > _EPS:
            self.points.append(value)

    def line_to(self, point: Vec3) -> WirePath:
        self._append(point)
        return self

    def line_by(self, dx: float, dy: float, dz: float) -> WirePath:
        self._append(_v_add(self.points[-1], (float(dx), float(dy), float(dz))))
        return self

    def bezier_to(
        self, control1: Vec3, control2: Vec3, end: Vec3, *, samples: int = 12
    ) -> WirePath:
        for point in sample_cubic_bezier_spline_3d(
            [self.points[-1], control1, control2, end], samples_per_segment=samples
        )[1:]:
            self._append(point)
        return self

    def arc(self, *, center: Vec3, normal: Vec3, angle: float, segments: int = 16) -> WirePath:
        for point in sample_arc_3d(
            start_point=self.points[-1],
            center=center,
            normal=normal,
            angle=angle,
            segments=segments,
        )[1:]:
            self._append(point)
        return self

    def extend(self, points: Iterable[Vec3]) -> WirePath:
        for point in points:
            self._append(point)
        return self

    def to_points(self) -> list[Vec3]:
        return list(self.points)


def superellipse_profile(
    width: float, height: float, exponent: float = 2.6, *, segments: int = 48
) -> list[Vec2]:
    width, height, exponent = float(width), float(height), float(exponent)
    if width <= 0.0 or height <= 0.0 or exponent <= 0.0:
        raise ValueError("width, height, and exponent must be positive")
    count = max(12, int(segments))
    power = 2.0 / exponent
    result: list[Vec2] = []
    for index in range(count):
        angle = 2.0 * math.pi * index / count
        cosine, sine = math.cos(angle), math.sin(angle)
        result.append(
            (
                math.copysign(width * 0.5 * abs(cosine) ** power, cosine),
                math.copysign(height * 0.5 * abs(sine) ** power, sine),
            )
        )
    return result


def rounded_rect_profile(
    width: float, height: float, radius: float, *, corner_segments: int = 6
) -> list[Vec2]:
    width, height, radius = float(width), float(height), float(radius)
    if width <= 0.0 or height <= 0.0:
        raise ValueError("width and height must be positive")
    if radius < 0.0 or radius > min(width, height) * 0.5:
        raise ValueError("radius must fit inside the rectangle")
    if radius <= _EPS:
        return [
            (-width * 0.5, -height * 0.5),
            (width * 0.5, -height * 0.5),
            (width * 0.5, height * 0.5),
            (-width * 0.5, height * 0.5),
        ]
    result: list[Vec2] = []
    count = max(1, int(corner_segments))
    centers = (
        (width * 0.5 - radius, -height * 0.5 + radius, -math.pi * 0.5),
        (width * 0.5 - radius, height * 0.5 - radius, 0.0),
        (-width * 0.5 + radius, height * 0.5 - radius, math.pi * 0.5),
        (-width * 0.5 + radius, -height * 0.5 + radius, math.pi),
    )
    for corner, (center_x, center_y, start_angle) in enumerate(centers):
        for index in range(count + 1):
            if corner and index == 0:
                continue
            angle = start_angle + math.pi * 0.5 * index / count
            result.append(
                (center_x + radius * math.cos(angle), center_y + radius * math.sin(angle))
            )
    return result


def superellipse_side_loft(
    sections: Sequence[tuple[float, float, float, float]],
    *,
    exponents: float | Sequence[float] = 2.8,
    segments: int = 56,
    cap: bool = True,
    closed: bool = True,
    min_height: float = 0.0001,
    min_width: float = 0.0001,
) -> MeshGeometry:
    if len(sections) < 2:
        raise ValueError("superellipse_side_loft requires at least two sections")
    exponent_values = (
        [float(exponents)] * len(sections)
        if isinstance(exponents, (int, float))
        else [float(value) for value in exponents]
    )
    if len(exponent_values) != len(sections):
        raise ValueError("exponents must match the section count")
    profiles: list[list[Vec3]] = []
    for index, (y, z0, z1, width) in enumerate(sections):
        low, high = sorted((float(z0), float(z1)))
        height = max(float(min_height), high - low)
        section_width = max(float(min_width), float(width))
        center = 0.5 * (low + high)
        profiles.append(
            [
                (x, float(y), center + local_z)
                for x, local_z in superellipse_profile(
                    section_width,
                    height,
                    max(0.2, exponent_values[index]),
                    segments=max(12, int(segments)),
                )
            ]
        )
    return LoftGeometry(profiles, cap=cap, closed=closed)


class _SideLoftKwargs(TypedDict, total=False):
    segments: int
    cap: bool
    closed: bool
    min_height: float
    min_width: float


def split_superellipse_side_loft(
    sections: Sequence[tuple[float, float, float, float]],
    *,
    split_y: float,
    exponents: float | Sequence[float] = 2.8,
    **kwargs: Unpack[_SideLoftKwargs],
) -> tuple[MeshGeometry, MeshGeometry, tuple[float, float, float, float]]:
    exponent_values = (
        [float(exponents)] * len(sections)
        if isinstance(exponents, (int, float))
        else [float(value) for value in exponents]
    )
    if len(exponent_values) != len(sections):
        raise ValueError("exponents must match the section count")
    rows_with_exponents = list(zip(sections, exponent_values, strict=True))
    rows_with_exponents.sort(key=lambda item: float(item[0][0]))
    rows = [
        (float(row[0]), float(row[1]), float(row[2]), float(row[3]))
        for row, _exponent in rows_with_exponents
    ]
    exponent_values = [float(exponent) for _row, exponent in rows_with_exponents]
    split_y = float(split_y)
    if len(rows) < 3 or not rows[0][0] < split_y < rows[-1][0]:
        raise ValueError("split_y must lie strictly inside at least three sections")
    seam: tuple[float, float, float, float] | None = None
    seam_exponent = 0.0
    for index, row in enumerate(rows):
        if abs(row[0] - split_y) <= 1e-9:
            seam, seam_exponent = row, exponent_values[index]
            break
        if index + 1 < len(rows) and row[0] < split_y < rows[index + 1][0]:
            amount = (split_y - row[0]) / (rows[index + 1][0] - row[0])
            seam = (
                split_y,
                row[1] + (rows[index + 1][1] - row[1]) * amount,
                row[2] + (rows[index + 1][2] - row[2]) * amount,
                row[3] + (rows[index + 1][3] - row[3]) * amount,
            )
            seam_exponent = (
                exponent_values[index]
                + (exponent_values[index + 1] - exponent_values[index]) * amount
            )
            break
    if seam is None:
        raise ValueError("could not interpolate the split section")
    lower = [row for row in rows if row[0] < split_y] + [seam]
    upper = [seam] + [row for row in rows if row[0] > split_y]
    lower_exponents = [
        exponent_values[index] for index, row in enumerate(rows) if row[0] < split_y
    ] + [seam_exponent]
    upper_exponents = [seam_exponent] + [
        exponent_values[index] for index, row in enumerate(rows) if row[0] > split_y
    ]
    return (
        superellipse_side_loft(lower, exponents=lower_exponents, **kwargs),
        superellipse_side_loft(upper, exponents=upper_exponents, **kwargs),
        seam,
    )


def resample_side_sections(
    sections: Sequence[tuple[float, float, float, float]],
    *,
    samples_per_span: int = 2,
    smooth_passes: int = 0,
    min_height: float = 0.0001,
    min_width: float = 0.0001,
) -> list[tuple[float, float, float, float]]:
    rows = sorted((float(row[0]), float(row[1]), float(row[2]), float(row[3])) for row in sections)
    if len(rows) < 2:
        raise ValueError("resample_side_sections requires at least two sections")
    merged: list[tuple[float, float, float, float]] = []
    for row in rows:
        if merged and abs(row[0] - merged[-1][0]) <= 1e-9:
            previous = merged[-1]
            merged[-1] = (
                row[0],
                (previous[1] + row[1]) * 0.5,
                (previous[2] + row[2]) * 0.5,
                (previous[3] + row[3]) * 0.5,
            )
        else:
            merged.append(row)
    spans = max(1, int(samples_per_span))
    dense: list[tuple[float, float, float, float]] = []
    for index in range(len(merged) - 1):
        for sample in range(spans):
            amount = sample / spans
            dense.append(
                (
                    merged[index][0] + (merged[index + 1][0] - merged[index][0]) * amount,
                    merged[index][1] + (merged[index + 1][1] - merged[index][1]) * amount,
                    merged[index][2] + (merged[index + 1][2] - merged[index][2]) * amount,
                    merged[index][3] + (merged[index + 1][3] - merged[index][3]) * amount,
                )
            )
    dense.append(merged[-1])
    for _ in range(max(0, int(smooth_passes))):
        dense = [
            dense[0],
            *[
                (
                    dense[index][0],
                    0.25 * dense[index - 1][1] + 0.5 * dense[index][1] + 0.25 * dense[index + 1][1],
                    0.25 * dense[index - 1][2] + 0.5 * dense[index][2] + 0.25 * dense[index + 1][2],
                    0.25 * dense[index - 1][3] + 0.5 * dense[index][3] + 0.25 * dense[index + 1][3],
                )
                for index in range(1, len(dense) - 1)
            ],
            dense[-1],
        ]
    result = []
    for y, z0, z1, width in dense:
        low, high = sorted((z0, z1))
        if high - low < min_height:
            center = (low + high) * 0.5
            low, high = center - min_height * 0.5, center + min_height * 0.5
        result.append((y, low, high, max(float(min_width), width)))
    return result


__all__ = [
    "WirePath",
    "resample_side_sections",
    "rounded_rect_profile",
    "sample_arc_3d",
    "sample_catmull_rom_spline_2d",
    "sample_catmull_rom_spline_3d",
    "sample_cubic_bezier_spline_2d",
    "sample_cubic_bezier_spline_3d",
    "split_superellipse_side_loft",
    "superellipse_profile",
    "superellipse_side_loft",
]
