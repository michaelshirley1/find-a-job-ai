.PHONY: build dev-api dev-web deps-jobspy clean

# Build React then embed into Go binary
build:
	cd web && npm install && npm run build
	go build -o find-a-job .

# Run Go API server (dev)
dev-api:
	go run .

# Run React dev server with API proxy (dev)
dev-web:
	cd web && npm run dev

# Install the Python deps JobSpy needs (scripts/jobspy_search.py, called via internal/jobspy)
deps-jobspy:
	pip install -r scripts/requirements.txt

clean:
	rm -rf web/dist web/node_modules
	rm -f find-a-job find-a-job.exe
