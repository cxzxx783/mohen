#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
check_draft.py —— 稿子质检脚本（zhangwen-craft 库2/库3 的机器版）
纯标准库，无需第三方依赖。

用法:
    python check_draft.py <稿子文件路径> [--platform xhs|toutiao|gzh]

输出:
    完整中文报告写到 稿子同目录下 的 xxx_质检报告.txt (UTF-8)
    控制台只打印一行 ASCII 摘要(防 GBK 乱码)

检查项:
    第一层 12硬门槛(可自动化的9项) + 人查项提示
    第二层 AI词/标志词/合规词 扫描
    第三层 节奏体检(句长/段长/标点/emoji)
"""
import sys
import os
import re
import io

# ---------- 词表(与 lib/02-去AI味写作.md 的2.2/2.3/2.7 保持一致) ----------

# 硬门槛1: 二人称
PRONOUN_YOU = ["你", "您"]

# 硬门槛2: 路标词(合计应<=2)
ROAD_SIGNS = ["更关键", "换句话说", "事实上", "总之", "由此可见", "不难看出"]

# 硬门槛4: 讲义动作(应=0)
LECTURE_WORDS = ["拆一拆", "盘一盘", "说白了", "本质上", "聊一聊", "捋一捋", "拆解一下", "讲一讲"]

# 硬门槛5: 高频句式(合计应<=2)
HIGH_FREQ_PATTERNS = [
    (r"一旦.{0,12}就", "一旦…就"),
    (r"只有.{0,12}才", "只有…才"),
    (r"随着.{0,10}(发展|进步|变化)", "随着…发展"),
    (r"不仅.{0,10}更", "不仅…更是"),
    (r"不是.{0,10}而是", "不是…而是"),
]

# 硬门槛6: 戏剧化揭露(应=0)
DRAMA_WORDS = ["遮羞布", "揭穿真相", "真相是", "内幕", "惊天", "震惊"]

# 第二层: AI高频词(库2的2.2表)
AI_WORDS = [
    "此外", "另外", "至关重要", "赋能", "助力", "深入探讨", "无缝", "丝滑",
    "宝贵的", "充满活力", "总的来说", "综上所述", "值得注意的是", "需要强调",
    "一系列", "各种方式", "不可或缺", "大幅提升", "显著改善", "全方位", "多维度",
    "众所周知", "旨在", "闭环", "抓手", "打造生态", "首先其次最后", "希望能帮到你",
    "欢迎咨询", "随着时代的发展", "在当今",
]

# 第二层: 连接词(库3的3.1)
CONNECTORS = ["此外", "因此", "然而", "由此可见"]

# 第二层: 合规高危词(库2的2.7，简版扫描，命中需人工判断语境)
COMPLIANCE_WORDS = [
    "最好", "顶级", "唯一", "国家级", "全网最低", "100%", "永久", "必火",
    "治疗", "治愈", "消炎", "祛痘", "抗炎", "修复受损", "药用", "疗效", "医美级",
    "美白", "淡斑", "抗衰", "去皱", "生发", "瘦脸", "溶脂",
    "减肥", "排毒", "提高免疫力", "降血糖", "助眠", "无副作用",
    "智商税", "骗子", "假货", "黑心", "垃圾",
    "三天见效", "稳赚", "包过", "躺赚", "月入",
    "微信号", "VX", "私聊我", "加群", "淘口令", "二维码",
]

# ---------- 工具函数 ----------


def read_text(path):
    """先按 UTF-8 读，失败再 GBK。"""
    for enc in ("utf-8", "utf-8-sig", "gbk"):
        try:
            with io.open(path, "r", encoding=enc) as f:
                return f.read()
        except UnicodeDecodeError:
            continue
    with io.open(path, "r", encoding="utf-8", errors="replace") as f:
        return f.read()


def count_occurrences(text, word):
    return text.count(word)


def find_all(text, words):
    """返回 {词: 次数}，只保留命中的。"""
    hits = {}
    for w in words:
        n = text.count(w)
        if n:
            hits[w] = n
    return hits


def find_patterns(text, patterns):
    hits = {}
    for pat, label in patterns:
        found = re.findall(pat, text)
        if found:
            hits[label] = len(found)
    return hits


# 文件头元信息行(稿号/状态/配图/标签/分隔线)——不算正文，别混进质检
META_LINE = re.compile(
    r"^\s*(【.+?】.*|状态[:：].*|配图[:：].*|话题标签[:：].*|标签[:：].*|"
    r"题目[:：].*|标题[:：].*|平台[:：].*|日期[:：].*|字数[:：].*|"
    r"作者[:：].*|正文.*|—{2,}.*|={2,}.*|#.*)$"
)


def strip_meta(text):
    """剥掉开头连续的元信息行，只留正文。"""
    lines = text.split("\n")
    start = 0
    for i, line in enumerate(lines):
        s = line.strip()
        if not s:
            continue
        if META_LINE.match(s):
            start = i + 1
        else:
            break
    return "\n".join(lines[start:])


def split_paragraphs(text):
    """按空行切段，去掉标题行(以#开头)。
    若原文是一行一段、没用空行分隔，则退化为按行切段。"""
    def clean(raw_list):
        out = []
        for p in raw_list:
            p = p.strip()
            if not p:
                continue
            p = "\n".join(line for line in p.split("\n")
                          if not line.strip().startswith("#"))
            p = p.strip()
            if p:
                out.append(p)
        return out

    paras = clean(re.split(r"\n\s*\n", text))
    chinese = len(re.findall(r"[一-龥]", text))
    # 段落数过少但字数不少 → 原文没空行分段，改按行切
    if len(paras) <= 2 and chinese > 300:
        paras = clean(text.split("\n"))
    return paras


def split_sentences(text):
    """按中文句末标点切句。"""
    body = re.sub(r"[#*>`\-]{1,}", "", text)
    sents = re.split(r"[。！？!?；;…\n]+", body)
    return [s.strip() for s in sents if len(s.strip()) >= 2]


def mark(ok, warn=False):
    return "√" if ok else ("⚠️" if warn else "×")


# ---------- 各项检查 ----------


def check_12_gates(text, paras, sents):
    """第一层: 12硬门槛中可自动化的部分。"""
    lines = []

    # 1 二人称
    you = count_occurrences(text, "你")
    nyou = count_occurrences(text, "您")
    total_you = you + nyou
    lines.append("1. 二人称  %s  「你」%d次、「您」%d次（合格线：≤1次）%s"
                 % (mark(total_you <= 1), you, nyou,
                    "  ← 删「你」换客观口气/具体角色" if total_you > 1 else ""))

    # 2 路标词
    roads = find_all(text, ROAD_SIGNS)
    n_road = sum(roads.values())
    lines.append("2. 路标词  %s  合计%d次（合格线：≤2次）%s"
                 % (mark(n_road <= 2), n_road,
                    "  ← " + "、".join("%s×%d" % (k, v) for k, v in roads.items()) if roads else ""))

    # 3 冒号模板(句内"概念:解释"密度)
    colon = len(re.findall(r"[一-龥]{2,8}[:：][一-龥]", text))
    lines.append("3. 冒号模板  %s  「XX：解释」式%d处（合格线：≤2处）%s"
                 % (mark(colon <= 2), colon,
                    "  ← 冒号后内容融进句子" if colon > 2 else ""))

    # 4 讲义动作
    lec = find_all(text, LECTURE_WORDS)
    n_lec = sum(lec.values())
    lines.append("4. 讲义动作  %s  %d处（合格线：0处）%s"
                 % (mark(n_lec == 0), n_lec,
                    "  ← " + "、".join("%s×%d" % (k, v) for k, v in lec.items()) if lec else ""))

    # 5 高频句式
    hf = find_patterns(text, HIGH_FREQ_PATTERNS)
    n_hf = sum(hf.values())
    lines.append("5. 高频句式  %s  合计%d次（合格线：≤2次）%s"
                 % (mark(n_hf <= 2), n_hf,
                    "  ← " + "、".join("%s×%d" % (k, v) for k, v in hf.items()) if hf else ""))

    # 6 戏剧化揭露
    drama = find_all(text, DRAMA_WORDS)
    n_drama = sum(drama.values())
    lines.append("6. 戏剧化揭露  %s  %d处（合格线：0处）%s"
                 % (mark(n_drama == 0), n_drama,
                    "  ← " + "、".join("%s×%d" % (k, v) for k, v in drama.items()) if drama else ""))

    # 7 段落同构(连续3段开头模式相近)
    same_open = 0
    for i in range(len(paras) - 2):
        heads = [paras[j][:4] for j in range(i, i + 3)]
        if len(set(heads)) == 1 or all(re.match(r"^[①②③④⑤1-9第]", h) for h in heads):
            same_open += 1
    lines.append("7. 段落同构  %s  连续3段开头雷同%d处（合格线：0处）%s"
                 % (mark(same_open == 0), same_open,
                    "  ← 换一段用「场景+感受」或「反例+吐槽」开头" if same_open else ""))

    # 8 段落等厚(段长标准差)
    if paras:
        lens = [len(p) for p in paras]
        avg = sum(lens) / len(lens)
        var = sum((x - avg) ** 2 for x in lens) / len(lens)
        std = var ** 0.5
        thin = std < 40
        lines.append("8. 段落等厚  %s  段长均值%.0f字/标准差%.0f（合格线：标准差≥40，太齐=机器感）%s"
                     % (mark(not thin), avg, std,
                        "  ← 合并相邻两段 / 拆半句成独立短段" if thin else ""))
    else:
        lines.append("8. 段落等厚  ⚠️  未识别到段落结构")

    # 9 段尾收束(段尾抽象结论词)
    tail_words = ["因此", "所以说", "总之", "可见", "综上", "这意味着", "说明"]
    n_tail = 0
    for p in paras:
        if any(p.rstrip("。！？~ ").endswith(w) for w in tail_words):
            n_tail += 1
    lines.append("9. 段尾收束  %s  段尾补抽象结论%d段（合格线：0段）%s"
                 % (mark(n_tail == 0), n_tail,
                    "  ← 停在具体事实/场景/引语" if n_tail else ""))

    # 10 破折号
    dash = len(re.findall(r"—{1,}", text))
    lines.append("10. 破折号  %s  %d处（合格线：每段≤1处、全文宜≤2处）%s"
                 % (mark(dash <= 2), dash, "  ← 换句号/逗号" if dash > 2 else ""))

    # 11 粗体滥用
    bold = len(re.findall(r"\*\*.+?\*\*", text))
    lines.append("11. 粗体  %s  **加粗**%d处（合格线：≤2处）%s"
                 % (mark(bold <= 2), bold,
                    "  ← 发布稿要去掉所有Markdown符号" if bold else ""))

    # 12 标点单一
    comma = len(re.findall(r"[，,]", text))
    period = len(re.findall(r"[。.]", text))
    other = len(re.findall(r"[！？!?；;~～]", text))
    total_p = comma + period + other
    ratio = (comma + period) / total_p if total_p else 1
    lines.append("12. 标点单一  %s  逗号+句号占比%.0f%%，其他标点%d处（合格线：其他标点≥3处）%s"
                 % (mark(other >= 3), ratio * 100, other,
                    "  ← 插反问/感叹/短句停顿" if other < 3 else ""))

    return lines


# 需按语境精确匹配的合规词(避免"第一个/第一天"这类正常表达误报)
COMPLIANCE_PATTERNS = [
    (r"第[一1](名|品牌|选择|梯队)", "第一/第一名"),
    (r"全网(最|第一)", "全网最/第一"),
    (r"没有之一", "没有之一"),
    (r"排名第[一1]", "排名第一"),
]


def check_words(text):
    """第二层: 词表扫描。"""
    ai = find_all(text, AI_WORDS)
    conn = find_all(text, CONNECTORS)
    comp = find_all(text, COMPLIANCE_WORDS)
    for k, v in find_patterns(text, COMPLIANCE_PATTERNS).items():
        comp[k] = comp.get(k, 0) + v
    return ai, conn, comp


def check_rhythm(text, sents, paras):
    """第三层: 节奏体检。"""
    lines = []

    # 连续5句长度相近
    flat = 0
    if len(sents) >= 5:
        for i in range(len(sents) - 4):
            window = [len(s) for s in sents[i:i + 5]]
            if max(window) - min(window) <= 5:
                flat += 1
    lines.append("· 句长变化  %s  连续5句长度相近%d处（合格线：0处）"
                 % (mark(flat == 0), flat))

    # 长句后是否跟短句
    if sents:
        long_after_short = 0
        for i in range(len(sents) - 1):
            if len(sents[i]) > 30 and len(sents[i + 1]) > 30:
                long_after_short += 1
        lines.append("· 长短交替  %s  连续长句(>30字)%d处（越少越好）"
                     % (mark(long_after_short <= 1), long_after_short))

    # 超长段落
    over = [i + 1 for i, p in enumerate(paras) if len(p) > 120]
    lines.append("· 段落长度  %s  超过120字的段落%d段%s"
                 % (mark(not over), len(over),
                    ("  ← 第%s段" % "、".join(map(str, over))) if over else ""))

    # emoji
    emoji = re.findall(
        "[\U0001F300-\U0001FAFF\U00002600-\U000027BF\U0001F1E6-\U0001F1FF⬀-⯿️]",
        text)
    lines.append("· emoji    %d个（小红书合格线：8-15个、2-4行1个；长文平台可少用）" % len(emoji))

    # 首行长度(小红书开头3行生死线)
    first_line_len = len(sents[0]) if sents else 0
    lines.append("· 首句长度  %d字%s" % (first_line_len,
                                     "  ← 开头宜短促有钩子" if first_line_len > 45 else ""))

    # 总字数
    chinese = len(re.findall(r"[一-龥]", text))
    lines.append("· 中文字数  %d字" % chinese)

    return lines, chinese


# ---------- 主流程 ----------


def main():
    if len(sys.argv) < 2:
        print("Usage: python check_draft.py <draft.txt> [--platform xhs|toutiao|gzh]")
        return 1

    path = sys.argv[1]
    platform = "xhs"
    if "--platform" in sys.argv:
        i = sys.argv.index("--platform")
        if i + 1 < len(sys.argv):
            platform = sys.argv[i + 1]

    if not os.path.isfile(path):
        print("ERROR: file not found")
        return 1

    text = strip_meta(read_text(path))
    paras = split_paragraphs(text)
    sents = split_sentences(text)

    gates = check_12_gates(text, paras, sents)
    ai, conn, comp = check_words(text)
    rhythm, char_count = check_rhythm(text, sents, paras)

    # 汇总
    fails = [g for g in gates if "  ×  " in g]
    warns = [g for g in gates if "⚠️" in g]

    out = []
    out.append("=" * 56)
    out.append("稿子质检报告   (zhangwen-craft 库2/库3 机器版)")
    out.append("文件: %s" % os.path.basename(path))
    out.append("平台档位: %s   中文字数: %d" % (platform, char_count))
    out.append("=" * 56)

    out.append("\n【第一层】12硬门槛（可自动化9项，第7/8/9项为结构近似判断，仍需人眼确认）")
    out.extend(gates)
    out.append("  ⚠️ 需人工确认项：段落是否「一段一事」、是否有具体细节和「我」的视角(门槛人味部分)")

    out.append("\n【第二层】词表扫描")
    out.append("· AI高频词  共%d处 %s" % (sum(ai.values()),
                                       ("  ← " + "、".join("%s×%d" % (k, v) for k, v in ai.items())) if ai else "  √ 干净"))
    out.append("· 连接词    共%d处（每处-5分，见库3的3.1）%s"
               % (sum(conn.values()),
                  ("  ← " + "、".join("%s×%d" % (k, v) for k, v in conn.items())) if conn else "  √ 干净"))
    out.append("· 合规高危词 %d处（命中≠违规，需看语境；极限词/功效词按库2的2.7改主观体感）%s"
               % (sum(comp.values()),
                  ("\n     ← " + "、".join("%s×%d" % (k, v) for k, v in comp.items())) if comp else "  √ 未命中"))

    out.append("\n【第三层】节奏体检")
    out.extend(rhythm)

    # 结论
    out.append("\n【结论】")
    if fails:
        out.append("  🔴 硬门槛未过 %d 项，立刻改完再往下走：" % len(fails))
        for f in fails:
            out.append("     " + f.split("  ", 1)[0])
    else:
        out.append("  ✅ 可自动化硬门槛全部通过")
    if warns:
        out.append("  ⚠️ 需人工确认 %d 项" % len(warns))
    out.append("  提示：机器只查「像不像AI」，查不出「有没有真东西」——")
    out.append("       真实细节、具体数字、个人经历仍需按库2的2.4人工补。")

    report = "\n".join(out)

    # 写 UTF-8 文件(中文不 print 控制台，防 GBK 乱码)
    base = os.path.splitext(path)[0]
    out_path = base + "_质检报告.txt"
    with io.open(out_path, "w", encoding="utf-8") as f:
        f.write(report)

    print("OK. Report written to: %s" % out_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
