$env:PYTHONPATH = "D:\For studying-or-working\Develop-Project\recruit-system-V1.0;D:\For studying-or-working\Develop-Project\recruit-system-V1.0\packages\recruitment_ai_core"
Set-Location "D:\For studying-or-working\Develop-Project\recruit-system-V1.0"
python -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000 --reload
