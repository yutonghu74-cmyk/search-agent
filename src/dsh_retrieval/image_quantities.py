"""Ground image quantities without confusing objects in a scene with image totals."""
from decimal import Decimal
import re

NUMBER = r'(?:[1-9]\d{0,2}(?:,\d{3})+|\d+(?:\.\d{1,4})?万|\d{1,5}|[零〇一二两三四五六七八九十百千万]{1,10})'


def number_value(raw):
    raw = raw.replace(',', '')
    if raw.endswith('万') and re.fullmatch(r'\d+(?:\.\d{1,4})?万', raw):
        return int(Decimal(raw[:-1]) * 10000)
    if raw.isdigit():
        return int(raw)
    digits = dict(zip('零〇一二两三四五六七八九', (0, 0, 1, 2, 2, 3, 4, 5, 6, 7, 8, 9)))
    total = section = digit = 0
    for char in raw:
        if char in digits:
            digit = digits[char]
        elif char == '万':
            total += (section + digit) * 10000
            section = digit = 0
        else:
            section += (digit or 1) * {'十': 10, '百': 100, '千': 1000}[char]
            digit = 0
    return total + section + digit


def image_count_evidence(message, *, answering=False):
    """Return explicitly supplied quantities, not arbitrary numbers or inferred defaults."""
    patterns = [rf'(?<![\d,.\-])({NUMBER})\s*(?:来|左右)?\s*(?:张|幅|个\s*(?:图片|照片|图)|图片|照片|图|pics?\b|pictures?\b|photos?\b|images?\b)',
                rf'(?:数量|张数)\s*[:：]\s*({NUMBER})(?![\d.]|,\d)',
                rf'(?:图|图片|照片)\s*(?:来个|来|要)\s*({NUMBER})(?:[吧呢。！!\s]|$)']
    if answering:
        patterns.append(rf'^\s*(?:要|需要|共|总共)?\s*({NUMBER})(?:来)?\s*(?:张)?(?:就行|即可|吧)?[。.!！]?\s*$')
    counts = {number_value(match[1]) for pattern in patterns
              for match in re.finditer(pattern, message, re.I)}
    return {count for count in counts if 1 <= count <= 10000}


def relative_image_count(message, current_count):
    match = re.fullmatch(rf'\s*(再加|增加|追加|减少|减去|减)\s*({NUMBER})\s*张(?:图片|照片)?[吧。.!！]?\s*', message)
    if not match or current_count is None:
        return None
    delta = number_value(match[2]) * (-1 if match[1] in ('减少', '减去', '减') else 1)
    result = current_count + delta
    return result if 1 <= result <= 10000 else None


def is_agreement(message):
    return bool(re.fullmatch(r'\s*(?:嗯+|好的?[，,\s]*)?(?:是|是的|对|对的|没错|正确|可以|确认)[。！!\s]*', message))
