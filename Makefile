.PHONY: install seed run test bench bench-openai datasets demo docker
install:  ; pip install -r requirements-dev.txt
seed:     ; python -m scripts.seed
run:      ; uvicorn api.main:app --reload --port 8000
test:     ; pytest
datasets: ; python -m evaluation.generate_datasets
bench:    ; python -m evaluation.benchmark
bench-openai: ; python -m evaluation.benchmark --llm openai
demo:     ; python -m scripts.demo
docker:   ; docker compose up --build
