"""Fail-closed parser for a complete, user-quoted replacement term list."""

import re

from .expansion_models import MAX_EXPANSIONS, preview_edited_groups


_COUNT = r"(?P<count>\d{1,3}|[一二三四五六七八九十两]{1,3})"
_PREFIX = re.compile(r"^(?:把|将)?检索词(?:减少到|改成|设为)" + _COUNT + r"个?[，,\s]*保留(?P<list>.+)$")
_TERM_LIST = re.compile(r"^[“‘](?P<first>[^”’]{1,80})[”’](?:\s*[、，,和及]\s*[“‘][^”’]{1,80}[”’])*$")
_QUOTED = re.compile(r"[“‘]([^”’]{1,80})[”’]")
_TAIL = re.compile(r"(?:[，,。\s]*(?:其他需求和?\d*张目标不变|其他需求不变|目标不变))?[。\s]*$")
_REPLACE = re.compile(r"^(?:把|将)(?:检索词[中里]的?)?[“‘](?P<old>[^”’]{1,80})[”’]"
                      r"(?:替换成|改成|换成)[“‘](?P<new>[^”’]{1,80})[”’](?P<tail>.*)$")
_DIGITS = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
           "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}


def _count(value: str) -> int | None:
    if value.isdigit():
        return int(value)
    if value in _DIGITS:
        return _DIGITS[value]
    if value.startswith("十") and len(value) == 2 and value[1] in _DIGITS:
        return 10 + _DIGITS[value[1]]
    if value.endswith("十") and len(value) == 2 and value[0] in _DIGITS:
        return _DIGITS[value[0]] * 10
    return None


def explicit_term_list(text: str, groups: list[dict], *, target_count: int | None = None) -> list[dict] | None:
    """Return a single reviewed group only when the entire edit is unambiguous.

    The quoted list includes the existing primary label first; all later terms
    are auxiliary search terms. Any extra instruction falls back to the model.
    """
    if not isinstance(text, str) or len(groups) != 1:
        return None
    stated_target = re.search(r"(\d+)张目标不变", text)
    if stated_target and (target_count is None or int(stated_target.group(1)) != target_count):
        return None
    replacement = _REPLACE.fullmatch(text.strip())
    if replacement:
        if not _TAIL.fullmatch(replacement.group('tail')):
            return None
        old, new = replacement.group('old').strip(), replacement.group('new').strip()
        terms = list(groups[0]['auxiliary_terms'])
        if terms.count(old) != 1 or not new or new in terms or new == groups[0]['primary_label']:
            return None
        terms[terms.index(old)] = new
        edited = [{'primary_label': groups[0]['primary_label'], 'auxiliary_terms': terms}]
        try:
            preview_edited_groups(None, edited)
        except ValueError:
            return None
        return edited
    match = _PREFIX.fullmatch(text.strip())
    if not match:
        return None
    body = match.group("list")
    # The optional unchanged-target suffix is not part of the term list.
    suffix = re.search(r"[，,。]\s*(?:其他需求|目标)", body)
    if suffix:
        if not _TAIL.fullmatch(body[suffix.start():]):
            return None
        body = body[:suffix.start()]
    body = body.rstrip("。 ")
    if not _TERM_LIST.fullmatch(body):
        return None
    terms = [term.strip() for term in _QUOTED.findall(body)]
    expected = _count(match.group("count"))
    label = groups[0]["primary_label"]
    if (expected != len(terms) or not 1 <= expected <= MAX_EXPANSIONS
            or terms[0] != label or len(set(terms)) != len(terms)):
        return None
    edited = [{"primary_label": label, "auxiliary_terms": terms[1:]}]
    try:
        preview_edited_groups(None, edited)
    except ValueError:
        return None
    return edited
