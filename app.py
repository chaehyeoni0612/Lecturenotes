import streamlit as st
import fitz # PyMuPDF
import re
import os
import urllib.request

--- 한글 폰트 자동 다운로드 함수 ---
@st.cache_resource
def get_korean_font():
font_path = "NanumGothic.ttf"
if not os.path.exists(font_path):
# 구글 폰트에서 나눔고딕 다운로드
url = "https://github.com/google/fonts/raw/main/ofl/nanumgothic/NanumGothic-Regular.ttf"
urllib.request.urlretrieve(url, font_path)
return font_path

--- 대본 텍스트 파싱 함수 (문자열 기준) ---
def parse_script_text(text):
# 정규식을 이용해 '[O페이지] 내용' 구조를 추출
pattern = r'[(\d+)페이지](.*?)(?=[\d+페이지]|$)'
matches = re.findall(pattern, text, re.DOTALL)

script_dict = {}
for page_num_str, content in matches:
page_num = int(page_num_str)
script_dict[page_num] = content.strip()

return script_dict

--- 대본 파일 파싱 함수 (파일 업로드 기준) ---
def parse_script_bytes(txt_bytes):
# 윈도우 메모장(cp949)과 일반 UTF-8 인코딩 모두 대응
try:
text = txt_bytes.decode('utf-8')
except UnicodeDecodeError:
text = txt_bytes.decode('cp949')
return parse_script_text(text)

--- 메인 PDF 처리 함수 ---
def process_pdf_with_script(input_pdf_bytes, script_dict, progress_bar, status_text):
CM_TO_PT = 28.3465
A4_WIDTH = 21.0 * CM_TO_PT
A4_HEIGHT = 29.7 * CM_TO_PT
MARGIN_PT = 8.0 * CM_TO_PT

FINAL_WIDTH = A4_WIDTH + MARGIN_PT
FINAL_HEIGHT = A4_HEIGHT

font_path = get_korean_font()

doc = fitz.open(stream=input_pdf_bytes, filetype="pdf")
out_doc = fitz.open()
total_pages = len(doc)

for pno in range(total_pages):
page = doc[pno]
rect = page.rect
orig_width, orig_height = rect.width, rect.height

scale = min(A4_WIDTH / orig_width, A4_HEIGHT / orig_height)
scaled_width, scaled_height = orig_width * scale, orig_height * scale

dx = (A4_WIDTH - scaled_width) / 2
dy = (A4_HEIGHT - scaled_height) / 2

target_rect = fitz.Rect(dx, dy, dx + scaled_width, dy + scaled_height)
out_page = out_doc.new_page(width=FINAL_WIDTH, height=FINAL_HEIGHT)
out_page.show_pdf_page(target_rect, doc, pno)

# 텍스트 삽입 로직
current_page_num = pno + 1
if current_page_num in script_dict:
script_text = script_dict[current_page_num]

if script_text:
# 텍스트가 들어갈 상자 영역 지정 (오른쪽 8cm 여백 안쪽)
text_rect = fitz.Rect(
A4_WIDTH + 10, # x0
20, # y0
FINAL_WIDTH - 10, # x1
FINAL_HEIGHT - 20 # y1
)

# 폰트 등록 및 텍스트 삽입
out_page.insert_font(fontname="nanum", fontfile=font_path)
out_page.insert_textbox(
text_rect,
script_text,
fontsize=10,
fontname="nanum",
align=fitz.TEXT_ALIGN_LEFT
)

# 진행률 업데이트
progress = (pno + 1) / total_pages
progress_bar.progress(progress)
status_text.caption(f"대본 매칭 중... 📝 ({pno + 1} / {total_pages} 페이지 완료)")

out_bytes = out_doc.write()
doc.close()
out_doc.close()
return out_bytes

--- 🎨 Streamlit UI ---
st.set_page_config(page_title="PDF 대본 매칭기", page_icon="📘", layout="centered")

st.title("📘 PDF 여백 생성 & 강의 대본 매칭기")
st.markdown("PDF를 업로드하고 대본을 입력하면, 지정된 페이지 옆 여백에 텍스트를 자동으로 쏙 넣어줍니다.")
st.write("---")

1. PDF 업로드 영역
st.subheader("1️⃣ PDF 파일 업로드")
uploaded_pdf = st.file_uploader("변환할 PDF 파일을 올려주세요", type=["pdf"])

2. 대본 입력 영역 (탭 분리)
st.subheader("2️⃣ 강의 대본 입력 (선택)")
tab1, tab2 = st.tabs(["📋 텍스트 직접 붙여넣기", "📄 TXT 파일 업로드"])

with tab1:
pasted_text = st.text_area(
"클로바노트 등에서 복사한 대본을 여기에 붙여넣으세요.",
height=200,
placeholder="형식 예시:\n[1페이지] 첫 번째 슬라이드 내용입니다.\n[2페이지] 두 번째 슬라이드 설명...\n\n※ 빈칸으로 두면 여백만 생성됩니다."
)

with tab2:
uploaded_txt = st.file_uploader("또는 TXT 대본 파일을 업로드하세요", type=["txt"])

if uploaded_pdf is not None:
st.write("---")
if st.button("✨ PDF 변환 및 대본 매칭 실행", type="primary", use_container_width=True):
progress_bar = st.progress(0)
status_text = st.empty()

try:
# 대본 데이터를 가져올 우선순위 판단 (업로드 파일 > 붙여넣은 텍스트)
script_dict = {}
if uploaded_txt is not None:
txt_bytes = uploaded_txt.read()
script_dict = parse_script_bytes(txt_bytes)
elif pasted_text.strip():
script_dict = parse_script_text(pasted_text)

# PDF 변환 및 텍스트 매칭
input_bytes = uploaded_pdf.read()
output_bytes = process_pdf_with_script(input_bytes, script_dict, progress_bar, status_text)

status_text.text("🎉 모든 작업 완료!")
st.balloons()

st.download_button(
label="📥 필기용 PDF 다운로드",
data=output_bytes,
file_name=f"대본추가_{uploaded_pdf.name}",
mime="application/pdf",
use_container_width=True
)
except Exception as e:
st.error(f"오류가 발생했습니다: {e}")
