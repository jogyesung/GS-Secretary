FROM python:3.11-slim

WORKDIR /app

# 의존성 설치
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 슬라이드 PNG 렌더링용 Chromium + 시스템 라이브러리 설치
RUN playwright install --with-deps chromium

# 소스 복사
COPY . .

# 데이터 디렉토리 생성
RUN mkdir -p data/temp

CMD ["python", "-m", "bot.main"]
