.PHONY: build dev-api dev-web clean

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

clean:
	rm -rf web/dist web/node_modules
	rm -f find-a-job find-a-job.exe
