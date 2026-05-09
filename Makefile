.PHONY: install lock build deploy test clean

PY := .venv/bin/python

install:
	uv venv .venv --python 3.12
	uv pip install --python $(PY) -r requirements.txt

lock:
	uv pip compile requirements.in -o requirements.txt

build:
	sam build

deploy:
	sam deploy --stack-name anchor-x402 --capabilities CAPABILITY_IAM --resolve-s3 --no-confirm-changeset --no-fail-on-empty-changeset --region us-east-1

deploy-guided:
	sam deploy --stack-name anchor-x402 --capabilities CAPABILITY_IAM --resolve-s3 --guided

test:
	curl -s $$(jq -r '.[].Outputs[]|select(.OutputKey=="ApiUrl").OutputValue' .aws-sam/build/cfn-stack.json 2>/dev/null || echo http://localhost:8000)/health

local:
	$(PY) -m uvicorn app:app --reload --host 0.0.0.0 --port 8000

clean:
	rm -rf .aws-sam/ __pycache__ services/__pycache__
