package main

import (
	"embed"
	"log"

	"github.com/Bilius10/api_ai_gateway/internal/backendproxy"
	"github.com/wailsapp/wails/v2"
	"github.com/wailsapp/wails/v2/pkg/options"
	"github.com/wailsapp/wails/v2/pkg/options/assetserver"
)

//go:embed all:ui/dist
var assets embed.FS

func main() {
	apiMiddleware, err := backendproxy.New("http://127.0.0.1:8000")
	if err != nil {
		log.Fatal(err)
	}
	app := NewApp()
	err = wails.Run(&options.App{
		Title:       "AI Gateway",
		Width:       1280,
		Height:      820,
		MinWidth:    960,
		MinHeight:   640,
		AssetServer: &assetserver.Options{Assets: assets, Middleware: apiMiddleware},
		BackgroundColour: &options.RGBA{
			R: 7,
			G: 17,
			B: 15,
			A: 255,
		},
		OnStartup:  app.startup,
		OnShutdown: app.shutdown,
		Bind:       []interface{}{app},
		SingleInstanceLock: &options.SingleInstanceLock{
			UniqueId:               "b8b2f428-49b0-4e18-9bd0-17eb51bb81e4",
			OnSecondInstanceLaunch: app.onSecondInstanceLaunch,
		},
	})
	if err != nil {
		log.Fatal(err)
	}
}
