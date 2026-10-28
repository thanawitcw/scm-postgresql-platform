# SCM ETL — Windows-native tasks (see ops\*.ps1 for the real bodies).
# Usage with Git Bash / MSYS make, or read the commands and run the .ps1 directly.

migrate:
	alembic upgrade head

run:
	python -m etl run --date %date%

test:
	pytest -q

backup:
	powershell -ExecutionPolicy Bypass -File ops\backup.ps1
