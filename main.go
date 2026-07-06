package main

import (
	"embed"
	"io/fs"
	"log"
	"net/http"
)

//go:embed all:web/dist
var webFiles embed.FS

func main() {
	mux := http.NewServeMux()

	// TODO: register API routes here

	distFS, err := fs.Sub(webFiles, "web/dist")
	if err != nil {
		log.Fatal(err)
	}
	mux.Handle("/", spaHandler(http.FS(distFS)))

	log.Println("Listening on http://localhost:8080")
	log.Fatal(http.ListenAndServe(":8080", mux))
}

// spaHandler serves the React SPA, falling back to index.html for client-side routes.
func spaHandler(fsys http.FileSystem) http.Handler {
	fileServer := http.FileServer(fsys)
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		f, err := fsys.Open(r.URL.Path)
		if err != nil {
			r.URL.Path = "/"
		} else {
			f.Close()
		}
		fileServer.ServeHTTP(w, r)
	})
}
