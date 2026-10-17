import streamlit as st
try:
    import pymupdf as fitz          # PyMuPDF 1.24.3+
except ImportError:
    import fitz                     # 구버전 호환
import io
import re
import os
import base64
import urllib.request
import requests

LINE_RATIO = 1.32                   # 줄간격 배수
CM_TO_PT = 28.3465
A4_W, A4_H = 841.89, 595.28         # A4 가로 (pt) — 모든 슬라이드를 이 크기로 통일

# 글씨체: Pretendard (무료 OFL 폰트, jsDelivr 버전 고정 주소)
FONT_NAME = "scriptfont_hy"   # 강의록에 같은 이름 글꼴이 있으면 글자가 깨져서 고유하게 둠
FONT_FILE = "Pretendard-Regular.ttf"
FONT_URL = ("https://cdn.jsdelivr.net/npm/pretendard@1.3.9"
            "/dist/public/static/alternative/Pretendard-Regular.ttf")

# 굵은 글씨체 (핵심 상자 제목용)
FONT_BOLD_NAME = "scriptfont_hy_b"
FONT_BOLD_FILE = "Pretendard-Bold.ttf"
FONT_BOLD_URL = ("https://cdn.jsdelivr.net/npm/pretendard@1.3.9"
                 "/dist/public/static/alternative/Pretendard-Bold.ttf")

# 핵심 상자: 줄에 /// 만 있는 두 줄 사이의 내용
BOX_MARK = "///"
BOX_FILL = (0.88, 0.94, 1.0)
BOX_BORDER = (0.55, 0.72, 0.90)
BOX_BORDER_W = 0.6
BOX_PAD = 6                         # 상자 안쪽 여백 (pt)
BOX_BULLET = "•"
BULLET_RE = re.compile(r"^[-•·]\s+")
BOX_DROP_CHARS = {0xFE0F, 0x200D}   # 변형 선택자, 제로폭 접합자

# **강조** / ==강조== → 빨간 글씨 + 노란 형광펜,  #잡담# → 회색 글씨
STYLE_RE = re.compile(r"\*\*(.+?)\*\*|==(.+?)==|#([^#\n]+)#", re.S)
EMPH, GRAY, PLAIN = "emph", "gray", ""
EMPH_COLOR = (0.80, 0.05, 0.05)
EMPH_BG = (1.0, 0.93, 0.35)
GRAY_COLOR = (0.45, 0.45, 0.45)
TEXT_COLOR = (0, 0, 0)
STYLE_COLOR = {EMPH: EMPH_COLOR, GRAY: GRAY_COLOR, PLAIN: TEXT_COLOR}


def _is_valid_font(path):
    try:
        fitz.Font(fontfile=path)
        return True
    except Exception:
        return False


def _ensure_font(font_file, font_url):
    """글씨체 파일 확인 (없거나 깨졌으면 지우고 다시 받음)"""
    if os.path.exists(font_file) and _is_valid_font(font_file):
        return font_file
    tmp = font_file + ".part"
    urllib.request.urlretrieve(font_url, tmp)
    if not _is_valid_font(tmp):
        os.remove(tmp)
        raise RuntimeError("글씨체를 받지 못했습니다. 잠시 후 다시 시도해 주세요.")
    os.replace(tmp, font_file)
    return font_file


@st.cache_resource
def get_korean_font():
    """글씨체 자동 다운로드"""
    return _ensure_font(FONT_FILE, FONT_URL)


@st.cache_resource
def get_korean_bold_font():
    """굵은 글씨체 자동 다운로드"""
    return _ensure_font(FONT_BOLD_FILE, FONT_BOLD_URL)


class TextMeasurer:
    """글자 폭을 한 번만 재서 저장 (줄바꿈 계산 속도 핵심)"""

    def __init__(self, font_path):
        self.font = fitz.Font(fontfile=font_path)
        self.cache = {}

    def width(self, text, size):
        cache, font, total = self.cache, self.font, 0.0
        for ch in text:
            adv = cache.get(ch)
            if adv is None:
                adv = font.glyph_advance(ord(ch))
                cache[ch] = adv
            total += adv
        return total * size


def _cff_to_truetype(data):
    """OpenType-CFF 글꼴을 같은 글리프 순서의 TrueType(glyf) 글꼴로 변환"""
    from fontTools.ttLib import TTFont, newTable
    from fontTools.pens.ttGlyphPen import TTGlyphPen
    from fontTools.pens.cu2quPen import Cu2QuPen

    font = TTFont(io.BytesIO(data))
    order = font.getGlyphOrder()
    gs = font.getGlyphSet()
    glyphs = {}
    for name in order:
        pen = TTGlyphPen(None)
        try:
            gs[name].draw(Cu2QuPen(pen, max_err=1.0, reverse_direction=True))
            glyphs[name] = pen.glyph()
        except Exception:
            glyphs[name] = TTGlyphPen(None).glyph()

    glyf = newTable("glyf")
    glyf.glyphOrder = order
    glyf.glyphs = glyphs
    font["glyf"] = glyf
    font["loca"] = newTable("loca")
    for tag in ("CFF ", "VORG"):
        if tag in font:
            del font[tag]
    maxp = font["maxp"]
    maxp.tableVersion = 0x00010000
    for k in ("maxZones", "maxTwilightPoints", "maxStorage", "maxFunctionDefs",
              "maxInstructionDefs", "maxStackElements", "maxSizeOfInstructions",
              "maxComponentElements"):
        setattr(maxp, k, 0)
    maxp.maxZones = 1
    font["head"].glyphDataFormat = 0
    if "post" in font:
        font["post"].formatType = 3.0
    font.sfntVersion = "\x00\x01\x00\x00"
    out = io.BytesIO()
    font.save(out)
    return out.getvalue()


def repair_mislabeled_fonts(doc):
    """TrueType 로 표시돼 있지만 실제로는 CFF('OTTO')인 글꼴을 진짜 TrueType 으로 바꿔 끼운다.
    (삼성 노트 PDF 내보내기 문제: pdf.js·일부 태블릿 뷰어에서 해당 글자가 빈칸으로 보임)
    반환: 고친 글꼴 개수"""
    fixed = 0
    for xref in range(1, doc.xref_length()):
        try:
            if doc.xref_get_key(xref, "Type")[1] != "/FontDescriptor":
                continue
            t, v = doc.xref_get_key(xref, "FontFile2")
            if t != "xref":
                continue
            fx = int(v.split()[0])
            data = doc.xref_stream(fx)
            if data[:4] != b"OTTO":
                continue
            doc.update_stream(fx, _cff_to_truetype(data))
            fixed += 1
        except Exception:
            continue
    return fixed


def parse_script_text(text):
    splits = re.split(r'\[(\d+)\s*페이지\]', text)
    script_dict = {}
    for i in range(1, len(splits), 2):
        if i + 1 < len(splits):
            page_num = int(splits[i])
            content = splits[i + 1].strip()
            if page_num in script_dict:
                script_dict[page_num] += "\n" + content
            else:
                script_dict[page_num] = content
    return script_dict


def parse_script_bytes(txt_bytes):
    try:
        text = txt_bytes.decode('utf-8')
    except UnicodeDecodeError:
        text = txt_bytes.decode('cp949')
    return parse_script_text(text)


class Para(list):
    """문단: 단어 목록 + 종류(kind) + 핵심 상자 안 여부(in_box)
    kind: 'text'(대본) | 'box_title'(상자 제목, 굵게) | 'box_item'(상자 항목, • 붙음) | 'gap'(상자와 대본 사이 빈 줄)"""

    def __init__(self, words=(), kind="text", in_box=False):
        super().__init__(words)
        self.kind = kind
        self.in_box = in_box


def _tokenize_plain(text):
    """텍스트 → 문단별 단어 목록. 각 단어에 꾸밈 종류(강조/잡담)를 붙여서 반환"""
    chars, pos = [], 0
    for m in STYLE_RE.finditer(text):
        chars += [(c, PLAIN) for c in text[pos:m.start()]]
        inner = m.group(1) or m.group(2) or m.group(3)
        style = GRAY if m.group(3) else EMPH
        chars += [(c, style) for c in inner]
        pos = m.end()
    chars += [(c, PLAIN) for c in text[pos:]]

    paragraphs, words, cur, cur_style = [], [], "", PLAIN

    def flush_word():
        nonlocal cur, cur_style
        if cur:
            words.append([cur, cur_style])
            cur, cur_style = "", PLAIN

    for c, stl in chars:
        if c == "\n":
            flush_word()
            paragraphs.append(words)
            words = []
        elif c.isspace():
            flush_word()
        else:
            cur += c
            if cur_style != EMPH and stl:   # 강조가 잡담보다 우선
                cur_style = stl
    flush_word()
    paragraphs.append(words)
    return paragraphs


def _clean_chars(s, has_glyph=None, check_glyph=True):
    """이모지·변형 선택자·글꼴에 없는 글자를 지움 (has_glyph: 코드포인트 → 글리프 유무)"""
    out = []
    for ch in s:
        o = ord(ch)
        if o in BOX_DROP_CHARS:
            continue
        if ch.isspace():
            out.append(" ")
            continue
        if check_glyph:
            if o >= 0x1F000:                # 이모지 영역
                continue
            if has_glyph is not None and not has_glyph(o):
                continue
        out.append(ch)
    return "".join(out)


def _extract_boxes(text):
    """/// … /// 블록을 뽑아냄. 반환: (상자 안쪽 줄 목록들, 상자를 뺀 나머지 텍스트)
    짝이 없는 /// 한 줄은 일반 글자로 남김"""
    lines = text.split("\n")
    boxes, rest, i = [], [], 0
    while i < len(lines):
        if lines[i].strip() == BOX_MARK:
            j = next((k for k in range(i + 1, len(lines))
                      if lines[k].strip() == BOX_MARK), None)
            if j is not None:
                boxes.append(lines[i + 1:j])
                i = j + 1
                continue
        rest.append(lines[i])
        i += 1
    return boxes, "\n".join(rest)


def tokenize(text, has_glyph=None):
    """문단 → Para 목록. 핵심 상자(///)는 맨 위로 올려 box_title/box_item 문단으로 만든다"""
    boxes, rest = _extract_boxes(text)
    if not boxes:
        return [Para(w) for w in _tokenize_plain(text)]

    paras = []
    for blines in boxes:
        for raw in blines:
            s = raw.strip()
            if not s:
                continue
            m = BULLET_RE.match(s)
            if m:                           # 항목: 글자 지우기는 변형 선택자류만
                body = _clean_chars(s[m.end():], check_glyph=False).strip()
                words = _tokenize_plain(body)[0]
                kind = "box_item"
            else:                           # 제목: 이모지·글꼴에 없는 글자 제거
                body = _clean_chars(s, has_glyph).strip()
                words = _tokenize_plain(body)[0]
                kind = "box_title"
            if words:
                paras.append(Para(words, kind, True))

    rest = rest.strip()
    if rest:
        if paras:
            paras.append(Para([], "gap"))   # 상자와 대본 사이 한 줄
        paras += [Para(w) for w in _tokenize_plain(rest)]
    return paras


def fill_box(page, rect, tokens, state, font, size, bold_font=None):
    """사각형에 들어갈 만큼만 그리고 남은 위치를 반환. state = [문단번호, 단어번호]
    핵심 상자 문단은 안쪽 여백(BOX_PAD)을 두고 배경 사각형을 먼저 그린 뒤 글자를 그린다"""
    bold_font = bold_font or font
    lh = size * LINE_RATIO
    pad = BOX_PAD
    if rect.height < lh or rect.width <= size:
        return state, 0

    pi, wi = state
    space_w = font.width(" ", size)
    bullet = BOX_BULLET if font.font.has_glyph(ord(BOX_BULLET)) else "·"
    bullet_w = font.width(bullet + " ", size)

    lines = []      # 각 줄: dict(words, y, x, font, fname, bullet)
    runs = []       # 상자 줄이 이어진 구간 [위, 아래]
    used, prev_box = 0.0, False

    while pi < len(tokens):
        words = tokens[pi]
        kind = getattr(words, "kind", "text")
        in_box = getattr(words, "in_box", False)

        if kind == "gap" and not lines:     # 영역 맨 위의 간격 줄은 건너뜀
            pi += 1
            wi = 0
            continue

        lead = pad if in_box and not prev_box else 0        # 상자 시작 위 여백
        trail = pad if prev_box and not in_box else 0       # 상자 끝 아래 여백
        reserve = pad if in_box else 0                      # 이 줄이 마지막일 때 필요한 아래 여백
        if used + lead + trail + lh + reserve > rect.height + 1e-6:
            break
        used += lead + trail
        ytop = rect.y0 + used
        used += lh
        if in_box:
            if prev_box:
                runs[-1][1] = ytop + lh
            else:
                runs.append([ytop - pad, ytop + lh])
        prev_box = in_box

        if not words:                       # 빈 줄(문단 구분)
            lines.append({"words": [], "y": ytop})
            pi += 1
            wi = 0
            continue

        f = bold_font if kind == "box_title" else font
        x = rect.x0
        width = rect.width
        is_first = wi == 0
        if in_box:
            x += pad
            width -= 2 * pad
            if kind == "box_item":          # 걸어 내어쓰기: 줄바꿈된 줄은 • 뒤 글자에 맞춤
                x += bullet_w
                width -= bullet_w
        width = max(width, size)

        cur, cur_w = [], 0.0
        sp = f.width(" ", size)
        while wi < len(words):
            w, w_style = words[wi]
            w_w = f.width(w, size)
            add_w = w_w if not cur else sp + w_w
            if cur_w + add_w <= width:
                cur.append((w, w_style))
                cur_w += add_w
                wi += 1
            else:
                if not cur:                 # 한 단어가 통째로 너무 길면 글자 단위로 자름
                    k, acc = 0, 0.0
                    while k < len(w):
                        cw = f.width(w[k], size)
                        if acc + cw > width:
                            break
                        acc += cw
                        k += 1
                    k = max(1, k)
                    cur.append((w[:k], w_style))
                    words[wi] = [w[k:], w_style]
                break

        lines.append({"words": cur, "y": ytop, "x": x, "f": f,
                      "fname": FONT_BOLD_NAME if kind == "box_title" else FONT_NAME,
                      "bullet": kind == "box_item" and is_first,
                      "bx": rect.x0 + pad})
        if wi >= len(words):
            pi += 1
            wi = 0

    # 1) 상자 배경을 먼저 (글자·형광펜이 그 위에 올라오도록)
    for y0, y1 in runs:
        page.draw_rect(fitz.Rect(rect.x0 + 0.3, y0, rect.x1 - 0.3, y1 + pad),
                       color=BOX_BORDER, fill=BOX_FILL, width=BOX_BORDER_W)

    # 2) 글자
    for ln in lines:
        if not ln["words"]:
            continue
        y = ln["y"] + size
        f, fname = ln["f"], ln["fname"]
        if ln["bullet"]:
            page.insert_text(fitz.Point(ln["bx"], y), bullet, fontsize=size,
                             fontname=FONT_NAME, color=TEXT_COLOR)
        groups = []                         # 꾸밈이 같은 단어끼리 묶어서 그림
        for w, e in ln["words"]:
            if groups and groups[-1][1] == e:
                groups[-1][0] += " " + w
            else:
                groups.append([w, e])

        x = ln["x"]
        sp = f.width(" ", size)
        for gi, (text, style) in enumerate(groups):
            if gi:
                x += sp
            gw = f.width(text, size)
            if style == EMPH:
                page.draw_rect(
                    fitz.Rect(x - 0.5, y - size * 0.92, x + gw + 0.5, y + size * 0.26),
                    color=None, fill=EMPH_BG)
            page.insert_text(fitz.Point(x, y), text, fontsize=size,
                             fontname=fname, color=STYLE_COLOR[style])
            x += gw

    return [pi, wi], len(lines)


def is_done(tokens, state):
    pi, wi = state
    if pi >= len(tokens):
        return True
    return pi == len(tokens) - 1 and wi >= len(tokens[pi])


def place_slide(out_page, box, doc, pno, rot, disp_w, disp_h):
    """슬라이드를 box 안에 비율 유지하며 가운데 배치"""
    scale = min(box.width / disp_w, box.height / disp_h)
    sw, sh = disp_w * scale, disp_h * scale
    x0 = box.x0 + (box.width - sw) / 2
    y0 = box.y0 + (box.height - sh) / 2
    out_page.show_pdf_page(fitz.Rect(x0, y0, x0 + sw, y0 + sh), doc, pno, rotate=rot)


def process_pdf_with_script(input_pdf_bytes, script_dict, progress_bar, status_text,
                            margin_cm=8.0, font_size=11.0, margin_side="오른쪽",
                            max_extra_pages=12):
    MARGIN_PT = margin_cm * CM_TO_PT
    PAD = 12
    FINAL_W = A4_W + MARGIN_PT
    FINAL_H = A4_H

    if margin_side == "왼쪽":
        slide_box = fitz.Rect(MARGIN_PT, 0, FINAL_W, FINAL_H)
        text_box = fitz.Rect(PAD, 15, MARGIN_PT - PAD, FINAL_H - 15)
        divider_x = MARGIN_PT
    else:
        slide_box = fitz.Rect(0, 0, A4_W, FINAL_H)
        text_box = fitz.Rect(A4_W + PAD, 15, FINAL_W - PAD, FINAL_H - 15)
        divider_x = A4_W

    progress_bar.progress(0, text="글씨체 준비 중... 0%")
    font_path = get_korean_font()
    measure_font = TextMeasurer(font_path)
    bold_path = get_korean_bold_font()
    measure_bold = TextMeasurer(bold_path)

    progress_bar.progress(0, text="PDF 여는 중... 0%")
    doc = fitz.open(stream=input_pdf_bytes, filetype="pdf")

    # 삼성 노트 등에서 내보낸 PDF의 잘못 표시된 글꼴(→, ⊣ 같은 기호가 빈칸이 되는 원인)을 바로잡음
    progress_bar.progress(0, text="글꼴 점검 중... 0%")
    repair_mislabeled_fonts(doc)

    # 강의록에 달린 주석(필기, 메모, 화살표 등)을 페이지 본문으로 고정.
    # 이걸 안 하면 페이지를 옮겨 담을 때 주석이 통째로 사라진다.
    try:
        doc.bake()
    except AttributeError:
        pass

    out_doc = fitz.open()
    total_pages = len(doc)

    for pno in range(total_pages):
        page = doc[pno]

        # 회전 처리: /Rotate 를 지우고 show_pdf_page 에서 한 번만 적용
        # (/Rotate 는 시계 방향, show_pdf_page 의 rotate 는 반시계 방향이라 부호를 뒤집음)
        src_rot = page.rotation
        rot = (360 - src_rot) % 360
        page.set_rotation(0)
        raw = page.rect
        if src_rot in (90, 270):
            disp_w, disp_h = raw.height, raw.width
        else:
            disp_w, disp_h = raw.width, raw.height

        # 기본 페이지: A4 가로로 통일한 슬라이드 + 여백
        p1 = out_doc.new_page(width=FINAL_W, height=FINAL_H)
        p1.draw_rect(p1.rect, color=(1, 1, 1), fill=(1, 1, 1))
        place_slide(p1, slide_box, doc, pno, rot, disp_w, disp_h)
        p1.draw_line(fitz.Point(divider_x, 0), fitz.Point(divider_x, FINAL_H),
                     color=(0.8, 0.8, 0.8), width=0.5)

        script_text = script_dict.get(pno + 1, "").strip()
        if script_text:
            p1.insert_font(fontname=FONT_NAME, fontfile=font_path)
            p1.insert_font(fontname=FONT_BOLD_NAME, fontfile=bold_path)
            tokens = tokenize(script_text, measure_font.font.has_glyph)
            state = [0, 0]
            state, _ = fill_box(p1, text_box, tokens, state, measure_font, font_size,
                                  measure_bold)

            # 넘치면: 왼쪽 위 1/4 축소 슬라이드 + ㄱ자 영역에 이어쓰기
            mw, mh = FINAL_W / 2, FINAL_H / 2
            mini_box = fitz.Rect(0, 0, mw, mh)
            extra = 0
            while not is_done(tokens, state) and extra < max_extra_pages:
                pe = out_doc.new_page(width=FINAL_W, height=FINAL_H)
                pe.draw_rect(pe.rect, color=(1, 1, 1), fill=(1, 1, 1))
                pe.insert_font(fontname=FONT_NAME, fontfile=font_path)
                pe.insert_font(fontname=FONT_BOLD_NAME, fontfile=bold_path)

                place_slide(pe, mini_box, doc, pno, rot, disp_w, disp_h)
                pe.draw_rect(mini_box, color=(0.8, 0.8, 0.8), width=0.5)

                before = list(state)
                box_a = fitz.Rect(mw + PAD, 15, FINAL_W - PAD, mh - 5)
                state, _ = fill_box(pe, box_a, tokens, state, measure_font, font_size,
                                      measure_bold)
                if not is_done(tokens, state):
                    box_b = fitz.Rect(PAD, mh + 10, FINAL_W - PAD, FINAL_H - 15)
                    state, _ = fill_box(pe, box_b, tokens, state, measure_font, font_size,
                                          measure_bold)

                if state == before:          # 한 줄도 못 넣었으면 무한루프 방지
                    break
                extra += 1

        pct = int((pno + 1) / total_pages * 95)   # 나머지 5%는 저장 단계
        progress_bar.progress(pct / 100,
                              text=f"변환 중... {pct}% ({pno + 1} / {total_pages} 페이지)")

    progress_bar.progress(0.96, text="파일 저장 중... 96%")
    # subset_fonts() 는 쓰지 않는다: 강의록에 든 삼성 노트 글꼴 등을 줄이면서
    # 화살표(→)·⊣ 같은 기호가 크롬/태블릿 뷰어에서 빈칸이 된다.
    out_bytes = out_doc.write(garbage=3, deflate=True)
    progress_bar.progress(1.0, text="완료 100%")
    doc.close()
    out_doc.close()
    return out_bytes


# --- 구글 드라이브 (Apps Script: 대본 TXT / 폴더 목록 / 폴더 만들기 / 업로드) ---
def get_drive_config():
    try:
        cfg = st.secrets["drive"]
        return cfg["web_app_url"], cfg["secret"]
    except Exception:
        return None


def drive_call(payload, timeout=60):
    web_app_url, secret = get_drive_config()
    r = requests.post(web_app_url, json={"secret": secret, **payload}, timeout=timeout)
    try:
        info = r.json()
    except ValueError:
        raise RuntimeError("Apps Script 응답을 읽지 못했습니다. 웹 앱을 '모든 사용자' 권한으로, "
                           "새 버전으로 배포했는지 확인해 주세요.")
    if not info.get("ok"):
        raise RuntimeError(info.get("error", "알 수 없는 오류"))
    return info


def list_drive_txt():
    info = drive_call({"action": "txtlist"})
    return info.get("folderName", ""), info["files"]


def fetch_drive_txt(file_id):
    info = drive_call({"action": "txtget", "fileId": file_id}, timeout=120)
    return info["name"], base64.b64decode(info["base64"])


def list_drive_folders():
    info = drive_call({"action": "list"})
    return info["root"], info["folders"]


def create_drive_folder(name):
    return drive_call({"action": "mkdir", "name": name})


def upload_to_drive(pdf_bytes, file_name, progress_bar, folder_id=None, chunk_mb=8):
    total = len(pdf_bytes)

    progress_bar.progress(0, text="업로드 준비 중... 0%")
    info = drive_call({"action": "upload", "name": file_name, "size": total,
                       "folderId": folder_id})
    upload_url = info["uploadUrl"]
    folder_name = info.get("folderName", "")

    chunk = chunk_mb * 1024 * 1024          # 256KB의 배수여야 함
    start, retries = 0, 0
    while True:
        end = min(start + chunk, total) - 1
        try:
            resp = requests.put(
                upload_url, data=pdf_bytes[start:end + 1],
                headers={"Content-Range": f"bytes {start}-{end}/{total}"},
                allow_redirects=False, timeout=300)
        except requests.RequestException:
            resp = None

        if resp is not None and resp.status_code in (200, 201):
            progress_bar.progress(1.0, text="업로드 완료 100%")
            return f"https://drive.google.com/file/d/{resp.json().get('id')}/view", folder_name

        if resp is not None and resp.status_code == 308:      # 이어서 보낼 것
            rng = resp.headers.get("Range")
            start = int(rng.split("-")[1]) + 1 if rng else 0
            retries = 0
            pct = int(start / total * 100)
            progress_bar.progress(min(pct, 99) / 100,
                                  text=f"업로드 중... {pct}% "
                                       f"({start / 1e6:.1f} / {total / 1e6:.1f} MB)")
            continue

        retries += 1                         # 일시 오류면 받은 위치 확인 후 재시도
        if retries > 3 or (resp is not None and resp.status_code < 500):
            detail = (f"({resp.status_code}) {resp.text[:200]}" if resp is not None
                      else "네트워크 오류")
            raise RuntimeError(f"업로드 실패 {detail}")
        chk = requests.put(upload_url, headers={"Content-Range": f"bytes */{total}"},
                           allow_redirects=False, timeout=60)
        if chk.status_code in (200, 201):
            progress_bar.progress(1.0, text="업로드 완료 100%")
            return f"https://drive.google.com/file/d/{chk.json().get('id')}/view", folder_name
        rng = chk.headers.get("Range")
        start = int(rng.split("-")[1]) + 1 if rng else 0


# --- 화면 ---
st.set_page_config(page_title="PDF 대본 매칭기", layout="centered")
st.title("PDF 대본 매칭기")

st.subheader("1. 강의록 PDF")
uploaded_pdf = st.file_uploader("강의록 PDF", type=["pdf"], label_visibility="collapsed")

st.subheader("2. 대본")
tab_drive, tab_file, tab_paste = st.tabs(["드라이브", "TXT 업로드", "붙여넣기"])

with tab_drive:
    if get_drive_config() is None:
        st.caption("앱 설정(Secrets)에 [drive] 항목이 있어야 사용할 수 있습니다.")
    else:
        dc1, dc2 = st.columns([5, 1])
        with dc1:
            if "drive_txt_list" not in st.session_state:
                try:
                    with st.spinner("대본 목록 불러오는 중..."):
                        st.session_state["drive_txt_list"] = list_drive_txt()
                except Exception as e:
                    st.error(f"대본 목록을 불러오지 못했습니다: {e}")
        with dc2:
            st.write("")
            st.write("")
            if st.button("새로고침", key="txt_refresh", use_container_width=True):
                st.session_state.pop("drive_txt_list", None)
                st.rerun()

        if "drive_txt_list" in st.session_state:
            txt_folder_name, txt_files = st.session_state["drive_txt_list"]
            if not txt_files:
                st.info(f"'{txt_folder_name}' 폴더에 TXT 파일이 없습니다.")
            else:
                opts = ["(사용 안 함)"] + [f"{f['name']}  ·  {f['updated']}" for f in txt_files]
                pick = st.selectbox(f"{txt_folder_name} 폴더 (최근 수정 순)", opts,
                                    key="drive_txt_pick")
                if pick != "(사용 안 함)":
                    chosen = txt_files[opts.index(pick) - 1]
                    if st.session_state.get("drive_txt_id") != chosen["id"]:
                        try:
                            with st.spinner(f"{chosen['name']} 불러오는 중..."):
                                name, raw = fetch_drive_txt(chosen["id"])
                            st.session_state["drive_txt_id"] = chosen["id"]
                            st.session_state["drive_txt_data"] = (name, raw)
                        except Exception as e:
                            st.error(f"파일을 불러오지 못했습니다: {e}")
                else:
                    st.session_state.pop("drive_txt_id", None)
                    st.session_state.pop("drive_txt_data", None)

        if "drive_txt_data" in st.session_state:
            name, raw = st.session_state["drive_txt_data"]
            st.success(f"{name} · 페이지 태그 {len(parse_script_bytes(raw))}개")

with tab_file:
    uploaded_txt = st.file_uploader("대본 TXT", type=["txt"], label_visibility="collapsed")

with tab_paste:
    pasted_text = st.text_area(
        "대본 붙여넣기", height=200, label_visibility="collapsed",
        placeholder="[1페이지] 첫 번째 슬라이드 내용입니다.\n"
                    "[2페이지] **강조할 문장** 보통 글씨 #잡담#\n\n"
                    "비워 두면 여백만 생깁니다.")

st.subheader("3. 설정")
margin_side = st.radio("여백 위치", ["오른쪽", "왼쪽"], horizontal=True)
col1, col2 = st.columns(2)
with col1:
    margin_cm = st.slider("여백 크기 (cm)", 4.0, 12.0, 8.0, 0.5)
with col2:
    font_size = st.slider("대본 글자 크기 (pt)", 6.0, 16.0, 11.0, 0.5)

if uploaded_pdf is not None:
    if st.session_state.get("src_name") != uploaded_pdf.name:
        st.session_state["src_name"] = uploaded_pdf.name
        st.session_state.pop("output_bytes", None)

    if st.button("변환 실행", type="primary", use_container_width=True):
        progress_bar = st.progress(0, text="준비 중... 0%")
        status_text = st.empty()
        try:
            script_dict = {}
            if "drive_txt_data" in st.session_state:
                script_dict = parse_script_bytes(st.session_state["drive_txt_data"][1])
            elif uploaded_txt is not None:
                script_dict = parse_script_bytes(uploaded_txt.getvalue())
            elif pasted_text.strip():
                script_dict = parse_script_text(pasted_text)

            st.session_state.pop("output_bytes", None)
            st.session_state["run_id"] = st.session_state.get("run_id", 0) + 1
            st.session_state["output_bytes"] = process_pdf_with_script(
                uploaded_pdf.getvalue(), script_dict, progress_bar, status_text,
                margin_cm, font_size, margin_side)
            status_text.success("변환 완료")
        except Exception as e:
            st.error(f"오류가 발생했습니다: {e}")

    if "output_bytes" in st.session_state:
        st.subheader("4. 저장")
        default_name = f"대본추가_{os.path.splitext(uploaded_pdf.name)[0]}.pdf"
        out_name = st.text_input("파일 이름", value=default_name,
                                 key=f"out_name_{st.session_state.get('run_id', 0)}").strip()
        out_name = re.sub(r'[\\/:*?"<>|]', "_", out_name) or "대본추가"
        if not out_name.lower().endswith(".pdf"):
            out_name += ".pdf"

        st.download_button(f"{out_name} 다운로드", data=st.session_state["output_bytes"],
                           file_name=out_name, mime="application/pdf",
                           use_container_width=True)

        if get_drive_config() is not None:
            if "drive_folders" not in st.session_state:
                try:
                    with st.spinner("폴더 목록 불러오는 중..."):
                        st.session_state["drive_folders"] = list_drive_folders()
                except Exception as e:
                    st.error(f"폴더 목록을 불러오지 못했습니다: {e}")

            if "drive_folders" in st.session_state:
                root, children = st.session_state["drive_folders"]
                labels = {root["id"]: root["name"]}
                for f in children:
                    labels[f["id"]] = f"{root['name']} / {f['name']}"
                ids = list(labels.keys())

                if "drive_pending_sel" in st.session_state:
                    st.session_state["drive_folder_sel"] = st.session_state.pop("drive_pending_sel")
                if st.session_state.get("drive_folder_sel") not in ids:
                    st.session_state["drive_folder_sel"] = root["id"]

                c1, c2 = st.columns([5, 1])
                with c1:
                    folder_id = st.selectbox("저장할 폴더", ids, key="drive_folder_sel",
                                             format_func=lambda i: labels[i])
                with c2:
                    st.write("")
                    st.write("")
                    if st.button("새로고침", key="folder_refresh", use_container_width=True):
                        st.session_state.pop("drive_folders", None)
                        st.rerun()

                with st.expander(f"새 폴더 만들기 ({root['name']} 안에)"):
                    nk = st.session_state.get("drive_new_folder_n", 0)
                    new_name = st.text_input("폴더 이름", key=f"drive_new_folder_{nk}").strip()
                    if st.button("만들기", disabled=not new_name):
                        try:
                            res = create_drive_folder(new_name)
                            st.session_state["drive_folders"] = list_drive_folders()
                            st.session_state["drive_pending_sel"] = res["folder"]["id"]
                            st.session_state["drive_new_folder_n"] = nk + 1
                            st.rerun()
                        except Exception as e:
                            st.error(f"폴더를 만들지 못했습니다: {e}")

                up_key = f"drive_link_{st.session_state.get('run_id', 0)}_{folder_id}_{out_name}"
                if st.button(f"{labels[folder_id]}에 업로드", use_container_width=True):
                    up_bar = st.progress(0, text="업로드 준비 중... 0%")
                    try:
                        st.session_state[up_key] = upload_to_drive(
                            st.session_state["output_bytes"], out_name, up_bar, folder_id)
                    except Exception as e:
                        st.error(f"업로드 실패: {e}")
                if up_key in st.session_state:
                    link, folder_name = st.session_state[up_key]
                    st.success(f"{folder_name} 폴더에 저장됨 · [드라이브에서 열기]({link})")
