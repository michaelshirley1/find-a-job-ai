package models

type Job struct {
	ID          string `json:"id"`
	Title       string `json:"title"`
	Company     string `json:"company"`
	Location    string `json:"location"`
	Description string `json:"description"`
	URL         string `json:"url"`
	Status      string `json:"status"` // applied, interviewing, offered, rejected
	CreatedAt   string `json:"created_at"`
}
