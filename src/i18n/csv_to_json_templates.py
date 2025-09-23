import csv
import json
import os
import re
import sys
from typing import Dict, List


THIS_DIR = os.path.dirname(__file__)
CSV_PATH = os.path.join(THIS_DIR, 'Signal Bot - Signal Bot 1.6.csv')


def normalize_placeholder(text: str) -> str:
    replacements = {
        '{Agentname}': '{trader_name}',
        '{Pair}': '{pair}',
        '{Positionmode}': '{margin_type} ',
        '{Leverage}': '{leverage}X',
        '{Direction}': '{pair_side}',
        '{Price}': '{price}',
    }
    for k, v in replacements.items():
        text = text.replace(k, v)
    return text


def extract_lines(cell: str) -> List[str]:
    if cell is None:
        return []
    cell = cell.replace('\r\n', '\n').replace('\r', '\n')
    lines = [line.rstrip() for line in cell.split('\n')]
    return [line for line in lines if line.strip()]


def take_label_line(lines: List[str], starts_with: str) -> str:
    for line in lines:
        if line.strip().startswith(starts_with):
            # 去除像 方向 (多/空): 這種括號提示
            s = line.strip()
            s = re.sub(r"\s*\(.*?\)", "", s)
            return s
    return ''


_AR_RANGE = re.compile(r"[\u0600-\u06FF]")


def _extract_label_with_colon(raw: str, emoji: str, default_label: str) -> str:
    """更健壯地從一行像 "🛑 :سعر إيقاف الخسارة 🛑" 或 "✅ TP Price:" 萃取為
    "🛑 سعر إيقاف الخسارة:" 或 "✅ TP Price:"。處理 RTL 語言的冒號位置。"""
    if not raw:
        return default_label
    s = raw.strip()
    # 去除重複 emoji（只保留前綴）
    s = s.replace(emoji, '')
    if ':' in s:
        left, right = s.split(':', 1)
        left = left.strip()
        right = right.strip()
        # 如果右側包含阿拉伯字元，優先取右側
        if _AR_RANGE.search(right):
            base = right
        else:
            # 否則取較長且非空的一側作為標籤主體
            cand = right if len(right) >= len(left) else left
            base = cand or left or right
    else:
        base = s
    base = base.strip()
    return f"{emoji} {base}:" if base else default_label


def strip_after_link_marker(text: str) -> str:
    # remove trailing markers like ")", "(link)", "（link）" and extra arrows
    t = re.split(r"\(\s*link\s*\)|（\s*link\s*）", text, flags=re.IGNORECASE)[0]
    return t.strip().rstrip('> ').strip()


def build_copy_open(lines_by_lang: Dict[str, List[str]]) -> Dict[str, Dict[str, str]]:
    out: Dict[str, Dict[str, str]] = {}
    for lang, lines in lines_by_lang.items():
        header_line = lines[0] if lines else ''
        time_line = take_label_line(lines, '⏰')
        direction_line = take_label_line(lines, '➡️')
        entry_line = take_label_line(lines, '🎯')
        more_line = ''
        if lines:
            # last non-empty is typically the link text
            more_line = lines[-1]

        # Normalize placeholders
        header_line = normalize_placeholder(header_line)
        time_label = time_line.split(':')[0] + ':' if ':' in time_line else time_line
        dir_label = direction_line.split(':')[0] + ':' if ':' in direction_line else direction_line
        entry_label = entry_line.split(':')[0] + ':' if ':' in entry_line else entry_line

        # Direction 顯示多語方向（Long/Short 或 多/空），模板中保留 {pair_type} 由 handler 決定是否需要
        body = (
            f"{header_line}\n\n"
            f"📢{{pair}} {{margin_type}} {{leverage}}X\n"
            f"{time_label} {{formatted_time}} (UTC+0)\n"
            f"{dir_label} {{pair_side}}\n"
            f"{entry_label} ${{entry_price}}"
        )

        link_text = strip_after_link_marker(normalize_placeholder(more_line))
        # Ensure we keep the textual content but use markdown link target placeholder
        more = f"[{link_text}]({{detail_url}})" if link_text else ''

        out[lang] = {
            'body': body,
            'more': more,
        }
    return out


def build_trade_close(lines_by_lang: Dict[str, List[str]]) -> Dict[str, Dict[str, str]]:
    out: Dict[str, Dict[str, str]] = {}
    for lang, lines in lines_by_lang.items():
        header_line = lines[0] if lines else ''
        time_line = take_label_line(lines, '⏰')
        direction_line = take_label_line(lines, '➡️')
        roi_line = take_label_line(lines, '🙌🏻') or take_label_line(lines, '🙌')
        entry_line = take_label_line(lines, '🎯')
        exit_line = take_label_line(lines, '💰')

        header_line = normalize_placeholder(header_line)
        time_label = time_line.split(':')[0] + ':' if ':' in time_line else time_line
        dir_label = direction_line.split(':')[0] + ':' if ':' in direction_line else direction_line
        roi_label = roi_line.split(':')[0] + ':' if ':' in roi_line else roi_line
        entry_label = entry_line.split(':')[0] + ':' if ':' in entry_line else entry_line
        exit_label = exit_line.split(':')[0] + ':' if ':' in exit_line else exit_line

        body = (
            f"{header_line}\n\n"
            f"📢{{pair}} {{margin_type}} {{leverage}}X\n"
            f"{time_label} {{formatted_time}} (UTC+0)\n"
            f"{dir_label} {{pair_side}}\n"
            f"{roi_label} {{realized_pnl}}%\n"
            f"{entry_label} ${{entry_price}}\n"
            f"{exit_label} ${{exit_price}}"
        )

        out[lang] = {'body': body}
    return out


def build_scalp_setting(lines_by_lang: Dict[str, List[str]]) -> Dict[str, Dict[str, str]]:
    out: Dict[str, Dict[str, str]] = {}
    for lang, lines in lines_by_lang.items():
        header_line = lines[0] if lines else ''
        time_line = take_label_line(lines, '⏰')
        header_line = normalize_placeholder(header_line)
        time_label = time_line.split(':')[0] + ':' if ':' in time_line else time_line

        body = (
            f"{header_line}\n\n"
            f"📢{{pair}} {{pair_side}}\n"
            f"{time_label} {{formatted_time}} (UTC+0)"
        )

        # Lines for setting display（健壯處理，以修正阿語等 RTL 冒號混亂）
        tp_label_line = take_label_line(lines, '✅') or '✅ TP Price:'
        sl_label_line = take_label_line(lines, '🛑') or '🛑 SL Price:'
        tp_label = _extract_label_with_colon(tp_label_line, '✅', '✅ TP Price:')
        sl_label = _extract_label_with_colon(sl_label_line, '🛑', '🛑 SL Price:')

        out[lang] = {
            'body': body,
            'tp_set_line': f"{tp_label} ${{tp_price}}",
            'sl_set_line': f"{sl_label} ${{sl_price}}",
        }
    return out


def build_scalp_update(lines_by_lang: Dict[str, List[str]]) -> Dict[str, Dict[str, str]]:
    out: Dict[str, Dict[str, str]] = {}
    for lang, lines in lines_by_lang.items():
        header_line = lines[0] if lines else ''
        time_line = take_label_line(lines, '⏰')
        header_line = normalize_placeholder(header_line)
        time_label = time_line.split(':')[0] + ':' if ':' in time_line else time_line

        update_header = (
            f"{header_line}\n\n"
            f"📢{{pair}} {{pair_side}}\n"
            f"{time_label} {{formatted_time}} (UTC+0)"
        )

        # Find update lines for TP and SL and replace two {Price} occurrences
        def trans_update_line(prefix_emoji: str, prev_var: str, new_var: str) -> str:
            raw = take_label_line(lines, prefix_emoji)
            # 先取得正確的前綴標籤（含冒號）
            fixed_label = _extract_label_with_colon(raw, prefix_emoji, f"{prefix_emoji} ")
            text = normalize_placeholder(fixed_label)
            # replace first {price} -> previous, second -> new
            count = 0
            def repl(m):
                nonlocal count
                count += 1
                return f"${{{prev_var}}}" if count == 1 else f"${{{new_var}}}"
            return re.sub(r"\{price\}", repl, text, count=2)

        tp_update = trans_update_line('✅', 'previous_tp_price', 'tp_price') or '✅ TP Price: ${previous_tp_price} change to ${tp_price}'
        sl_update = trans_update_line('🛑', 'previous_sl_price', 'sl_price') or '🛑 SL Price: ${previous_sl_price} change to ${sl_price}'

        out[lang] = {
            'update_header': update_header,
            'tp_update_line': tp_update,
            'sl_update_line': sl_update,
        }
    return out


def build_holding_summary(lines_by_lang: Dict[str, List[str]], tp_labels: Dict[str, str], sl_labels: Dict[str, str]) -> Dict[str, Dict[str, str]]:
    out: Dict[str, Dict[str, str]] = {}
    for lang, lines in lines_by_lang.items():
        header_line = lines[0] if lines else ''
        direction_line = take_label_line(lines, '➡️')
        entry_line = take_label_line(lines, '🎯')
        price_line = take_label_line(lines, '📊')
        roi_line = take_label_line(lines, '🚀')

        header_line = normalize_placeholder(header_line)
        dir_label = direction_line.split(':')[0] + ':' if ':' in direction_line else direction_line
        entry_label = entry_line.split(':')[0] + ':' if ':' in entry_line else entry_line
        price_label = price_line.split(':')[0] + ':' if ':' in price_line else price_line
        roi_label = roi_line.split(':')[0] + ':' if ':' in roi_line else roi_line

        body = (
            f"{header_line}\n\n"
            f"📢{{pair}} {{margin_type}} {{leverage}}X\n"
            f"{dir_label} {{pair_side}}\n"
            f"{entry_label} ${{entry_price}}\n"
            f"{price_label} ${{current_price}}\n"
            f"{roi_label} {{roi}}%"
        )

        header = header_line

        tp_label = tp_labels.get(lang, '✅ TP Price:')
        sl_label = sl_labels.get(lang, '🛑 SL Price:')

        item = (
            f"**{{index}}. {{pair}} {{margin_type}} {{leverage}}X**\n"
            f"{dir_label} {{pair_side}}\n"
            f"{entry_label} ${{entry_price}}\n"
            f"{price_label} ${{current_price}}\n"
            f"{roi_label} {{roi}}%"
        )

        out[lang] = {
            'body': body,
            'header': header,
            'item': item,
            'tp_line': f"{tp_label} ${{tp_price}}",
            'sl_line': f"{sl_label} ${{sl_price}}",
        }
    return out


def build_weekly(lines_by_lang: Dict[str, List[str]]) -> Dict[str, Dict[str, str]]:
    out: Dict[str, Dict[str, str]] = {}
    for lang, lines in lines_by_lang.items():
        header_line = lines[0] if lines else ''
        roi_line = take_label_line(lines, '🔥')
        total_line = take_label_line(lines, '📈')
        win_line = take_label_line(lines, '✅')
        lose_line = take_label_line(lines, '❌')
        rate_line = take_label_line(lines, '🏆')

        header_line = normalize_placeholder(header_line)
        roi_label = roi_line.split(':')[0] + ':' if ':' in roi_line else roi_line
        total_label = total_line.split(':')[0] + ':' if ':' in total_line else total_line
        win_label = win_line.split(':')[0] + ':' if ':' in win_line else win_line
        lose_label = lose_line.split(':')[0] + ':' if ':' in lose_line else lose_line
        rate_label = rate_line.split(':')[0] + ':' if ':' in rate_line else rate_line

        body = (
            f"{header_line}\n\n"
            f"{roi_label} {{total_roi}}%\n\n"
            f"{total_label} {{total_trades}}\n"
            f"{win_label} {{win_trades}}\n"
            f"{lose_label} {{loss_trades}}\n"
            f"{rate_label} {{win_rate}}%"
        )

        # rank.item line uses a compact single block
        rank_item = (
            f"**{{rank}}. {{trader_name}}**\n"
            f"{roi_label} {{total_roi}}%\n"
            f"{total_label} {{total_trades}}\n"
            f"{win_label} {{win_trades}}\n"
            f"{lose_label} {{loss_trades}}\n"
            f"{rate_label} {{win_rate}}%\n"
        )

        out[lang] = {
            'header': header_line,
            'body': body,
            'rank_item': rank_item,
        }
    return out


def main():
    with open(CSV_PATH, 'r', encoding='utf-8-sig', newline='') as f:
        reader = csv.reader(f)
        rows = list(reader)

    if not rows:
        raise SystemExit('CSV is empty')

    header = rows[0]
    langs = header  # e.g., ['en','zh','ru',...]

    # Map each block row index to type by English marker
    blocks: Dict[str, Dict[str, List[str]]] = {
        'copy_open': {},
        'trade_close': {},
        'scalp_setting': {},
        'scalp_update': {},
        'holding_summary': {},
        'weekly': {},
    }

    # helper to assign parsed lines per lang
    def assign_block(block_key: str, row_cells: List[str]):
        for idx, lang in enumerate(langs):
            cell = row_cells[idx] if idx < len(row_cells) else ''
            blocks[block_key][lang] = extract_lines(cell)

    for row in rows[1:]:
        en_cell = row[0] if row else ''
        sample = (en_cell or '').lower()
        if 'new trade open' in sample:
            assign_block('copy_open', row)
        elif 'close position' in sample:
            assign_block('trade_close', row)
        elif 'tp/sl setting' in sample:
            assign_block('scalp_setting', row)
        elif 'tp/sl update' in sample:
            assign_block('scalp_update', row)
        elif 'trading summary' in sample:
            assign_block('holding_summary', row)
        elif 'weekly performance report' in sample:
            assign_block('weekly', row)

    # Build per section structures
    copy_open = build_copy_open(blocks['copy_open'])
    trade_close = build_trade_close(blocks['trade_close'])
    scalp_setting = build_scalp_setting(blocks['scalp_setting'])
    scalp_update = build_scalp_update(blocks['scalp_update'])

    # For holding summary tp/sl labels reuse scalp setting labels
    tp_labels = {lang: (take_label_line(lines, '✅').split(':')[0] + ':' if ':' in take_label_line(lines, '✅') else take_label_line(lines, '✅'))
                 for lang, lines in blocks['scalp_setting'].items()}
    sl_labels = {lang: (take_label_line(lines, '🛑').split(':')[0] + ':' if ':' in take_label_line(lines, '🛑') else take_label_line(lines, '🛑'))
                 for lang, lines in blocks['scalp_setting'].items()}
    holding_summary = build_holding_summary(blocks['holding_summary'], tp_labels, sl_labels)

    weekly = build_weekly(blocks['weekly'])

    # Assemble final per-language json structure
    # Optionally overwrite existing base templates via flag
    overwrite_base = ('--overwrite-base' in sys.argv) or bool(os.getenv('OVERWRITE_BASE'))
    skip_lang_files = set() if overwrite_base else {'en.json', 'zh-CN.json', 'zh-TW.json'}

    for lang in langs:
        # Determine output target files for each CSV language column
        filename = f"{lang}.json"
        if filename in skip_lang_files:
            continue

        target_filenames: List[str]
        if lang == 'zh':
            # Map CSV zh 到 both zh-TW/zh-CN
            target_filenames = ['zh-TW.json', 'zh-CN.json']
        else:
            target_filenames = [filename]

        data = {
            'copy': {
                'open': {
                    'body': copy_open.get(lang, {}).get('body', ''),
                    'more': copy_open.get(lang, {}).get('more', ''),
                }
            },
            'holding': {
                'summary': {
                    'body': holding_summary.get(lang, {}).get('body', ''),
                    'header': holding_summary.get(lang, {}).get('header', ''),
                    'item': holding_summary.get(lang, {}).get('item', ''),
                    'tp_line': holding_summary.get(lang, {}).get('tp_line', ''),
                    'sl_line': holding_summary.get(lang, {}).get('sl_line', ''),
                }
            },
            'trade': {
                'close': {
                    'body': trade_close.get(lang, {}).get('body', ''),
                }
            },
            'scalp': {
                'tp_sl': {
                    'body': scalp_setting.get(lang, {}).get('body', ''),
                    'update_header': scalp_update.get(lang, {}).get('update_header', ''),
                    'tp_update_line': scalp_update.get(lang, {}).get('tp_update_line', ''),
                    'tp_set_line': scalp_setting.get(lang, {}).get('tp_set_line', ''),
                    'sl_update_line': scalp_update.get(lang, {}).get('sl_update_line', ''),
                    'sl_set_line': scalp_setting.get(lang, {}).get('sl_set_line', ''),
                }
            },
            'weekly': {
                'report': {
                    'header': weekly.get(lang, {}).get('header', ''),
                    'body': weekly.get(lang, {}).get('body', ''),
                },
                'rank': {
                    'item': weekly.get(lang, {}).get('rank_item', ''),
                }
            },
        }

        # Write file(s)
        for out_name in target_filenames:
            out_path = os.path.join(THIS_DIR, out_name)
            with open(out_path, 'w', encoding='utf-8') as wf:
                json.dump(data, wf, ensure_ascii=False, indent=2)

    print('Done generating language templates from CSV.')


if __name__ == '__main__':
    main()


