"""Pure ``extract-v1.0`` rules for GPU model, memory, condition, and warranty text."""

import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from zoneinfo import ZoneInfo

RULE_VERSION = "extract-v1.0"

# Keys are canonical model_key values; values are the valid VRAM sizes in GB.
MODEL_TABLE: dict[str, frozenset[int]] = {
    "gtx-1650": frozenset({4}),
    "gtx-1660": frozenset({6}),
    "gtx-1660-super": frozenset({6}),
    "gtx-1660-ti": frozenset({6}),
    "rtx-2060": frozenset({6, 12}),
    "rtx-2060-super": frozenset({8}),
    "rtx-2070": frozenset({8}),
    "rtx-2070-super": frozenset({8}),
    "rtx-2080": frozenset({8}),
    "rtx-2080-super": frozenset({8}),
    "rtx-2080-ti": frozenset({11}),
    "rtx-3050": frozenset({6, 8}),
    "rtx-3060": frozenset({8, 12}),
    "rtx-3060-ti": frozenset({8}),
    "rtx-3070": frozenset({8}),
    "rtx-3070-ti": frozenset({8}),
    "rtx-3080": frozenset({10, 12}),
    "rtx-3080-ti": frozenset({12}),
    "rtx-3090": frozenset({24}),
    "rtx-3090-ti": frozenset({24}),
    "rtx-4060": frozenset({8}),
    "rtx-4060-ti": frozenset({8, 16}),
    "rtx-4070": frozenset({12}),
    "rtx-4070-super": frozenset({12}),
    "rtx-4070-ti": frozenset({12}),
    "rtx-4070-ti-super": frozenset({16}),
    "rtx-4080": frozenset({16}),
    "rtx-4080-super": frozenset({16}),
    "rtx-4090": frozenset({24}),
    "rx-6600": frozenset({8}),
    "rx-6600-xt": frozenset({8}),
    "rx-6650-xt": frozenset({8}),
    "rx-6700-xt": frozenset({12}),
    "rx-6750-xt": frozenset({12}),
    "rx-6800": frozenset({16}),
    "rx-6800-xt": frozenset({16}),
    "rx-6900-xt": frozenset({16}),
    "rx-7600": frozenset({8}),
    "rx-7700-xt": frozenset({12}),
    "rx-7800-xt": frozenset({16}),
    "rx-7900-xt": frozenset({20}),
    "rx-7900-xtx": frozenset({24}),
}


@dataclass(frozen=True)
class RuleResult:
    """One rule outcome; ``None`` from a rule means no tag should be written."""

    value: str | None
    reason: str | None = None


def normalize_text(value: str) -> str:
    """Apply the comparison normalization specified by extract-v1.0."""
    return " ".join(unicodedata.normalize("NFKC", value).lower().split())


def _model_parts(model_key: str) -> tuple[str, str, str]:
    family, remainder = model_key.split("-", maxsplit=1)
    number, _, suffix = remainder.partition("-")
    return family, number, suffix


_MODEL_TOKENS = re.compile(
    r"(?<![^\W_])"
    r"(?:(?P<geforce>geforce)\s*)?"
    r"(?:(?P<radeon>radeon)\s*(?=rx))?"
    r"(?P<family>rtx|gtx|rx)?\s*"
    r"(?P<number>\d{4})"
    r"(?:\s*(?P<suffix>ti\s*super|ti|super|xtx|xt))?"
    r"(?![^\W_])"
)
_KNOWN_MODEL_NUMBERS = {number for _, number, _ in map(_model_parts, MODEL_TABLE)}


def _resolve_model_token(match: re.Match[str]) -> tuple[str | None, bool]:
    """Return (model_key, recognized-but-unknown) for one syntactic token."""
    number = match.group("number")
    if number not in _KNOWN_MODEL_NUMBERS:
        return None, False

    suffix_text = match.group("suffix")
    suffix = re.sub(r"\s+", "-", suffix_text.lower()) if suffix_text else ""
    candidates = [
        model_key
        for model_key in MODEL_TABLE
        if _model_parts(model_key)[1] == number and _model_parts(model_key)[2] == suffix
    ]
    if not candidates:
        return None, True

    prefix_family = match.group("family")
    if match.group("geforce"):
        if prefix_family is not None and prefix_family not in {"rtx", "gtx"}:
            return None, True
        candidates = [key for key in candidates if _model_parts(key)[0] in {"rtx", "gtx"}]
    if match.group("radeon") and prefix_family != "rx":
        return None, True
    if prefix_family is not None:
        candidates = [key for key in candidates if _model_parts(key)[0] == prefix_family]
    if len(candidates) != 1:
        return None, True
    return candidates[0], False


def extract_model(text: str) -> RuleResult | None:
    """Extract a canonical model, or a documented ambiguous/unknown outcome."""
    normalized = normalize_text(text)
    model_keys: set[str] = set()
    found_unknown = False
    for match in _MODEL_TOKENS.finditer(normalized):
        model_key, unknown = _resolve_model_token(match)
        if model_key is not None:
            model_keys.add(model_key)
        found_unknown = found_unknown or unknown

    if len(model_keys) > 1:
        return RuleResult(None, "ambiguous_model")
    if found_unknown:
        return RuleResult(None, "unknown_model")
    if model_keys:
        return RuleResult(next(iter(model_keys)))
    return None


_VRAM_TOKENS = re.compile(r"(?<![A-Za-z0-9])(?P<value>\d{1,2})\s*(?:gb|g|กิ๊ก|กิก)(?![a-z])")


def _vram_values(text: str) -> set[int]:
    normalized = normalize_text(text)
    return {int(match.group("value")) for match in _VRAM_TOKENS.finditer(normalized)}


def extract_vram(text: str, model: RuleResult | None) -> RuleResult | None:
    """Validate an explicit VRAM value against a confirmed model in this item."""
    values = _vram_values(text)
    if not values:
        return None
    if model is None or model.value is None or model.reason is not None:
        return RuleResult(None, "model_unknown")
    if len(values) > 1:
        return RuleResult(None, "ambiguous_vram")
    value = next(iter(values))
    if value not in MODEL_TABLE[model.value]:
        return RuleResult(None, "vram_not_valid_for_model")
    return RuleResult(str(value))


_USED_PATTERN = re.compile(
    r"เหมือนใหม่|มือ\s*2|มือสอง|(?<![A-Za-z0-9])like new(?![A-Za-z0-9])|"
    r"(?<![A-Za-z0-9])used(?![A-Za-z0-9])|"
    r"(?<![A-Za-z0-9])second hand(?![A-Za-z0-9])|ผ่านการใช้งาน"
)
_NEW_PATTERN = re.compile(
    r"มือ\s*1|มือหนึ่ง|ของใหม่|ใหม่แกะกล่อง|ยังไม่แกะ|ซีล|"
    r"(?<![A-Za-z0-9])new(?![A-Za-z0-9])"
)
_UNUSED_PATTERN = re.compile(r"ไม่เคยใช้งาน|ไม่เคยใช้|ไม่เคยลงเครื่อง|ไม่เคยเสียบไฟ|เก่าเก็บ|ค้างสต็อก")

_WARRANTY_NONE_PATTERN = re.compile(r"ไม่มีประกัน|out of warranty|no warranty|หมดประกัน|ประกันหมด")
_WARRANTY_ACTIVE_PATTERN = re.compile(
    r"มีประกัน|ติดประกัน|ประกันเหลือ|ประกันถึง|ประกันศูนย์|ประกันร้าน|"
    r"(?<![A-Za-z0-9])warranty(?![A-Za-z0-9])"
)
_WARRANTY_DURATION_PATTERN = re.compile(r"เหลือ\s*(?P<number>\d+)\s*(?P<unit>เดือน|ปี)")

_THAI_MONTH_PATTERNS = [
    (1, r"มกราคม"),
    (1, r"ม\.?\s*ค\.?"),
    (2, r"กุมภาพันธ์"),
    (2, r"ก\.?\s*พ\.?"),
    (3, r"มีนาคม"),
    (3, r"มี\.?\s*ค\.?"),
    (4, r"เมษายน"),
    (4, r"เม\.?\s*ย\.?"),
    (5, r"พฤษภาคม"),
    (5, r"พ\.?\s*ค\.?"),
    (6, r"มิถุนายน"),
    (6, r"มิ\.?\s*ย\.?"),
    (7, r"กรกฎาคม"),
    (7, r"ก\.?\s*ค\.?"),
    (8, r"สิงหาคม"),
    (8, r"ส\.?\s*ค\.?"),
    (9, r"กันยายน"),
    (9, r"ก\.?\s*ย\.?"),
    (10, r"ตุลาคม"),
    (10, r"ต\.?\s*ค\.?"),
    (11, r"พฤศจิกายน"),
    (11, r"พ\.?\s*ย\.?"),
    (12, r"ธันวาคม"),
    (12, r"ธ\.?\s*ค\.?"),
]
_THAI_MONTH_PATTERN = "|".join(pattern for _month, pattern in _THAI_MONTH_PATTERNS)
_THAI_MONTH_NUMBERS = {
    "มกราคม": 1,
    "มค": 1,
    "กุมภาพันธ์": 2,
    "กพ": 2,
    "มีนาคม": 3,
    "มีค": 3,
    "เมษายน": 4,
    "เมย": 4,
    "พฤษภาคม": 5,
    "พค": 5,
    "มิถุนายน": 6,
    "มิย": 6,
    "กรกฎาคม": 7,
    "กค": 7,
    "สิงหาคม": 8,
    "สค": 8,
    "กันยายน": 9,
    "กย": 9,
    "ตุลาคม": 10,
    "ตค": 10,
    "พฤศจิกายน": 11,
    "พย": 11,
    "ธันวาคม": 12,
    "ธค": 12,
}
_WARRANTY_DATE_PATTERN = re.compile(
    rf"ถึง\s*(?:(?<!\d)(?P<numeric_month>\d{{1,2}})\s*/\s*"
    rf"(?P<numeric_year>\d{{2}}|\d{{4}})(?!\d)|"
    rf"(?P<thai_month>{_THAI_MONTH_PATTERN})\s*"
    rf"(?P<thai_year>\d{{2}}|\d{{4}})(?!\d))"
)


def _without_matches(text: str, matches: list[re.Match[str]]) -> str:
    remaining = list(text)
    for match in matches:
        remaining[match.start() : match.end()] = " " * (match.end() - match.start())
    return "".join(remaining)


def extract_condition(text: str, *, warranty_none: bool = False) -> RuleResult | None:
    """Classify condition, removing used phrases before looking for new phrases."""
    normalized = normalize_text(text)
    used_matches = list(_USED_PATTERN.finditer(normalized))
    remaining = _without_matches(normalized, used_matches)
    unused_matches = list(_UNUSED_PATTERN.finditer(remaining))
    found_new = _NEW_PATTERN.search(remaining) is not None or bool(unused_matches)
    found_used = bool(used_matches)

    if found_new and found_used:
        return RuleResult(None, "ambiguous_condition")
    if found_used:
        return RuleResult("used")
    if found_new:
        return RuleResult("new_no_warranty" if warranty_none else "new")
    return None


def extract_warranty(text: str) -> RuleResult | None:
    """Classify warranty after masking explicit no-warranty phrases."""
    normalized = normalize_text(text)
    none_matches = list(_WARRANTY_NONE_PATTERN.finditer(normalized))
    remaining = _without_matches(normalized, none_matches)
    found_active = _WARRANTY_ACTIVE_PATTERN.search(remaining) is not None
    found_none = bool(none_matches)

    if found_active and found_none:
        return RuleResult(None, "ambiguous_warranty")
    if found_none:
        return RuleResult("none")
    if found_active:
        return RuleResult("active")
    return None


def _month_text(year: int, month: int) -> str:
    return f"{year:04d}-{month:02d}"


def _add_months(base: date, months: int) -> str:
    month_index = base.year * 12 + base.month - 1 + months
    year, month_index = divmod(month_index, 12)
    return _month_text(year, month_index + 1)


def _year_from_text(value: str, base: date) -> int:
    year = int(value)
    if len(value) == 2:
        candidates = (2000 + year, 2500 + year - 543)
        return min(candidates, key=lambda candidate: abs(candidate - base.year))
    return year - 543 if year >= 2500 else year


def extract_warranty_until(text: str, base: date) -> RuleResult | None:
    """Return a warranty end month, or a documented invalid/ambiguous result."""
    normalized = normalize_text(text)
    months: set[str] = set()
    invalid = False

    for match in _WARRANTY_DURATION_PATTERN.finditer(normalized):
        amount = int(match.group("number"))
        unit = match.group("unit")
        if unit == "เดือน":
            if not 1 <= amount <= 120:
                invalid = True
                continue
            months.add(_add_months(base, amount))
        else:
            if not 1 <= amount <= 10:
                invalid = True
                continue
            months.add(_add_months(base, amount * 12))

    for match in _WARRANTY_DATE_PATTERN.finditer(normalized):
        numeric_month = match.group("numeric_month")
        month_text = match.group("thai_month")
        month = int(numeric_month) if numeric_month is not None else None
        if month_text is not None:
            month_key = re.sub(r"[.\s]", "", month_text)
            month = _THAI_MONTH_NUMBERS[month_key]
        if month is None or not 1 <= month <= 12:
            invalid = True
            continue

        year_text = match.group("numeric_year") or match.group("thai_year")
        if year_text is None:
            invalid = True
            continue
        year = _year_from_text(year_text, base)
        if not 1 <= year <= 9999:
            invalid = True
            continue
        months.add(_month_text(year, month))

    if len(months) > 1:
        return RuleResult(None, "ambiguous_warranty_until")
    if invalid:
        return RuleResult(None, "warranty_date_invalid")
    if months:
        return RuleResult(next(iter(months)))
    return None


def extract_warranty_state(text: str, base: date) -> tuple[RuleResult | None, RuleResult | None]:
    """Return warranty and end-month outcomes, marking already-ended coverage as none."""
    warranty = extract_warranty(text)
    if warranty is None or warranty.value != "active" or warranty.reason is not None:
        return warranty, None

    until = extract_warranty_until(text, base)
    if until is not None and until.value is not None:
        year_text, month_text = until.value.split("-", maxsplit=1)
        if (int(year_text), int(month_text)) < (base.year, base.month):
            warranty = RuleResult("none")
    return warranty, until


_BANGKOK = ZoneInfo("Asia/Bangkok")
_WARRANTY_BASE_PRECISIONS = {"exact", "minute", "hour", "day"}


def _time_value_as_bangkok_date(value: object) -> date | None:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, date):
        return value
    elif isinstance(value, str):
        try:
            if len(value) == 10:
                return date.fromisoformat(value)
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None

    if parsed.tzinfo is None or parsed.utcoffset() is None:
        parsed = parsed.replace(tzinfo=_BANGKOK)
    return parsed.astimezone(_BANGKOK).date()


def warranty_base_date(posted_at: Mapping[str, object] | None, observed_at: datetime) -> date:
    """Select the contract-defined observation timestamp as a Bangkok calendar date."""
    if posted_at is not None and posted_at.get("precision") in _WARRANTY_BASE_PRECISIONS:
        posted_date = _time_value_as_bangkok_date(posted_at.get("value"))
        if posted_date is not None:
            return posted_date
    observed_date = _time_value_as_bangkok_date(observed_at)
    if observed_date is None:
        raise ValueError("observed_at must be a valid timestamp")
    return observed_date
