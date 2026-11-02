"""Deterministic perceptual fingerprints and advisory near-duplicate review pairs."""

from dataclasses import dataclass
from hashlib import sha256
from io import BytesIO
import math
import re
import statistics
import warnings


ALGORITHM = "phash-64-luma@1"
POLICY_REF = "near-duplicate-review@1"
DEFAULT_REVIEW_DISTANCE = 6
_HEX64 = re.compile(r"[0-9a-f]{16}\Z")


@dataclass(frozen=True, slots=True)
class PerceptualFingerprint:
    candidate_id: str
    algorithm: str
    bit_length: int
    value: str
    width: int | None = None
    height: int | None = None
    sharpness: float | None = None

    def __post_init__(self):
        if (not isinstance(self.candidate_id, str) or not self.candidate_id
                or self.algorithm != ALGORITHM or self.bit_length != 64
                or not isinstance(self.value, str) or not _HEX64.fullmatch(self.value)
                or (self.width is None) != (self.height is None)
                or self.width is not None and (
                    type(self.width) is not int or type(self.height) is not int
                    or self.width < 1 or self.height < 1)
                or self.sharpness is not None and (
                    type(self.sharpness) is not float
                    or not math.isfinite(self.sharpness) or self.sharpness < 0)):
            raise ValueError("invalid perceptual fingerprint")


@dataclass(frozen=True, slots=True)
class NearDuplicatePair:
    candidate_id: str
    other_candidate_id: str
    hamming_distance: int
    review_threshold: int
    policy_ref: str = POLICY_REF

    def __post_init__(self):
        if (not isinstance(self.candidate_id, str) or not self.candidate_id
                or not isinstance(self.other_candidate_id, str) or not self.other_candidate_id
                or self.candidate_id >= self.other_candidate_id
                or type(self.hamming_distance) is not int
                or type(self.review_threshold) is not int
                or not 0 <= self.hamming_distance <= self.review_threshold <= 16
                or self.policy_ref != POLICY_REF):
            raise ValueError("invalid near-duplicate review pair")


@dataclass(frozen=True, slots=True)
class NearDuplicateCluster:
    cluster_id: str
    candidate_ids: tuple[str, ...]
    pairs: tuple[NearDuplicatePair, ...]
    recommended_candidate_id: str

    def __post_init__(self):
        if (not isinstance(self.cluster_id, str) or not self.cluster_id.startswith("ndc_")
                or not isinstance(self.candidate_ids, tuple) or len(self.candidate_ids) < 2
                or tuple(sorted(set(self.candidate_ids))) != self.candidate_ids
                or not isinstance(self.pairs, tuple) or not self.pairs
                or any(type(pair) is not NearDuplicatePair for pair in self.pairs)
                or self.recommended_candidate_id not in self.candidate_ids):
            raise ValueError("invalid near-duplicate cluster")


@dataclass(frozen=True, slots=True)
class NearDuplicateDecision:
    cluster_id: str
    candidate_ids: tuple[str, ...]
    action: str
    representative_candidate_id: str | None

    def __post_init__(self):
        if (not isinstance(self.cluster_id, str) or not self.cluster_id.startswith("ndc_")
                or not isinstance(self.candidate_ids, tuple)
                or len(self.candidate_ids) < 2
                or tuple(sorted(set(self.candidate_ids))) != self.candidate_ids
                or self.action not in {"merge", "keep_separate"}
                or (self.action == "merge" and self.representative_candidate_id not in self.candidate_ids)
                or (self.action == "keep_separate" and self.representative_candidate_id is not None)):
            raise ValueError("invalid near-duplicate decision")


def compute_perceptual_fingerprint(content: bytes, candidate_id: str):
    """Return a 64-bit pHash, or ``None`` when bytes are not a safe image.

    A missing fingerprint is advisory-unavailable and never changes a cleaning
    verdict or causes a candidate to be removed.
    """
    if not isinstance(content, bytes) or not isinstance(candidate_id, str) or not candidate_id:
        raise TypeError("image bytes and candidate identity required")
    try:
        from PIL import Image, ImageOps, UnidentifiedImageError
    except ImportError:
        return None
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(content)) as opened:
                if opened.width < 1 or opened.height < 1 or opened.width * opened.height > 50_000_000:
                    return None
                normalized = ImageOps.exif_transpose(opened).convert("L")
                width, height = normalized.size
                sharpness_image = normalized.resize((min(256, width), min(256, height)),
                                                    Image.Resampling.LANCZOS)
                sharpness_pixels = tuple(sharpness_image.get_flattened_data())
                sharpness_width, sharpness_height = sharpness_image.size
                edge_energy = 0
                edge_count = 0
                for y in range(1, sharpness_height):
                    row, prior = y * sharpness_width, (y - 1) * sharpness_width
                    for x in range(1, sharpness_width):
                        value = sharpness_pixels[row + x]
                        edge_energy += abs(value - sharpness_pixels[row + x - 1])
                        edge_energy += abs(value - sharpness_pixels[prior + x])
                        edge_count += 2
                sharpness = float(edge_energy / edge_count) if edge_count else 0.0
                image = normalized.resize(
                    (32, 32), Image.Resampling.LANCZOS)
                pixels = tuple(image.get_flattened_data())
    except (OSError, ValueError, UnidentifiedImageError, Warning):
        return None

    cosines = tuple(tuple(math.cos(math.pi * (2 * position + 1) * frequency / 64)
                          for position in range(32)) for frequency in range(8))
    coefficients = []
    for vertical in range(8):
        for horizontal in range(8):
            value = 0.0
            for y in range(32):
                row = y * 32
                vertical_weight = cosines[vertical][y]
                value += vertical_weight * sum(
                    pixels[row + x] * cosines[horizontal][x] for x in range(32))
            normalizer = (math.sqrt(0.5) if horizontal == 0 else 1.0) * (
                math.sqrt(0.5) if vertical == 0 else 1.0)
            coefficients.append(value * normalizer)
    median = statistics.median(coefficients[1:])
    bits = 0
    for coefficient in coefficients:
        bits = (bits << 1) | int(coefficient > median)
    return PerceptualFingerprint(candidate_id, ALGORITHM, 64, f"{bits:016x}",
                                 width, height, sharpness)


def hamming_distance(left: PerceptualFingerprint, right: PerceptualFingerprint):
    if type(left) is not PerceptualFingerprint or type(right) is not PerceptualFingerprint:
        raise TypeError("perceptual fingerprints required")
    if left.algorithm != right.algorithm or left.bit_length != right.bit_length:
        raise ValueError("fingerprint algorithms differ")
    return (int(left.value, 16) ^ int(right.value, 16)).bit_count()


def assess_near_duplicates(fingerprints, *, review_threshold=DEFAULT_REVIEW_DISTANCE):
    """Return stable advisory pairs; never chooses a winner or changes counts."""
    if (not isinstance(fingerprints, tuple) or type(review_threshold) is not int
            or not 0 <= review_threshold <= 16
            or any(type(item) is not PerceptualFingerprint for item in fingerprints)
            or len({item.candidate_id for item in fingerprints}) != len(fingerprints)):
        raise ValueError("invalid near-duplicate assessment input")
    ordered = tuple(sorted(fingerprints, key=lambda item: item.candidate_id))
    pairs = []
    for index, left in enumerate(ordered):
        for right in ordered[index + 1:]:
            distance = hamming_distance(left, right)
            if distance <= review_threshold:
                pairs.append(NearDuplicatePair(
                    left.candidate_id, right.candidate_id, distance, review_threshold))
    return tuple(pairs)


def build_near_duplicate_clusters(pairs, fingerprints):
    """Group unresolved pair edges and recommend a deterministic quality winner."""
    if (not isinstance(pairs, tuple) or not isinstance(fingerprints, tuple)
            or any(type(item) is not NearDuplicatePair for item in pairs)
            or any(type(item) is not PerceptualFingerprint for item in fingerprints)):
        raise ValueError("invalid near-duplicate cluster input")
    by_id = {item.candidate_id: item for item in fingerprints}
    adjacency = {}
    for pair in pairs:
        if pair.candidate_id not in by_id or pair.other_candidate_id not in by_id:
            raise ValueError("near-duplicate pair fingerprint is missing")
        adjacency.setdefault(pair.candidate_id, set()).add(pair.other_candidate_id)
        adjacency.setdefault(pair.other_candidate_id, set()).add(pair.candidate_id)
    clusters = []
    unseen = set(adjacency)
    while unseen:
        root = min(unseen)
        members, frontier = set(), [root]
        while frontier:
            candidate_id = frontier.pop()
            if candidate_id in members:
                continue
            members.add(candidate_id)
            frontier.extend(adjacency[candidate_id] - members)
        unseen -= members
        candidate_ids = tuple(sorted(members))
        member_pairs = tuple(pair for pair in pairs
                             if pair.candidate_id in members
                             and pair.other_candidate_id in members)
        cluster_id = "ndc_" + sha256("\0".join(candidate_ids).encode()).hexdigest()[:24]
        def quality(candidate_id):
            fingerprint = by_id[candidate_id]
            pixels = ((fingerprint.width or 0) * (fingerprint.height or 0))
            return (-pixels, -(fingerprint.sharpness or 0.0), candidate_id)
        recommended = min(candidate_ids, key=quality)
        clusters.append(NearDuplicateCluster(
            cluster_id, candidate_ids, member_pairs, recommended))
    return tuple(sorted(clusters, key=lambda item: item.cluster_id))
