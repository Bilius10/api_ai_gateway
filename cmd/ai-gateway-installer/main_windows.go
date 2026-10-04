package main

import (
	"embed"
	"errors"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strconv"
	"strings"
	"syscall"
	"unsafe"
)

const productName = "AI Gateway"

//go:embed payload/*
var payloadFS embed.FS

func main() {
	if hasArg("--verify") {
		if _, err := applicationPayload(); err != nil {
			os.Exit(1)
		}
		return
	}
	if hasArg("--uninstall") {
		if confirm("Desinstalar o AI Gateway?") {
			if err := uninstall(); err != nil {
				showError("Não foi possível desinstalar o AI Gateway:\n\n" + err.Error())
				os.Exit(1)
			}
			showInfo("AI Gateway desinstalado.")
		}
		return
	}
	if err := install(); err != nil {
		showError("Não foi possível instalar o AI Gateway:\n\n" + err.Error())
		os.Exit(1)
	}
}

func install() error {
	if runtime.GOARCH != "amd64" {
		return fmt.Errorf("esta versão requer Windows AMD64; arquitetura detectada: %s", runtime.GOARCH)
	}
	data, err := applicationPayload()
	if err != nil {
		return err
	}
	directory, err := installationDirectory()
	if err != nil {
		return err
	}
	if err := os.MkdirAll(directory, 0o755); err != nil {
		return fmt.Errorf("criar diretório de instalação: %w", err)
	}
	application := filepath.Join(directory, "AI Gateway.exe")
	if err := replaceFile(application, data); err != nil {
		return err
	}
	uninstaller := filepath.Join(directory, "Uninstall AI Gateway.exe")
	if err := copySelf(uninstaller); err != nil {
		return err
	}
	if err := createStartMenuShortcut(directory, application); err != nil {
		return err
	}
	if err := registerUninstaller(directory, application, uninstaller); err != nil {
		return err
	}
	showInfo("AI Gateway instalado com sucesso.")
	return exec.Command(application).Start()
}

func uninstall() error {
	directory, err := installationDirectory()
	if err != nil {
		return err
	}
	_ = removeStartMenuShortcut()
	_ = hiddenRun("reg.exe", "delete", `HKCU\Software\Microsoft\Windows\CurrentVersion\Uninstall\AIGateway`, "/f")
	if err := os.Remove(filepath.Join(directory, "AI Gateway.exe")); err != nil && !errors.Is(err, os.ErrNotExist) {
		return err
	}
	scheduleSelfRemoval(directory)
	return nil
}

func applicationPayload() ([]byte, error) {
	data, err := payloadFS.ReadFile("payload/AI Gateway.exe")
	if err != nil {
		return nil, fmt.Errorf("ler aplicativo incorporado: %w", err)
	}
	if len(data) < 1<<20 {
		return nil, errors.New("o aplicativo incorporado está incompleto")
	}
	return data, nil
}

func installationDirectory() (string, error) {
	root := os.Getenv("LOCALAPPDATA")
	if root == "" {
		return "", errors.New("LOCALAPPDATA não está definido")
	}
	return filepath.Join(root, "Programs", productName), nil
}

func replaceFile(destination string, data []byte) error {
	temporary := destination + ".new"
	backup := destination + ".old"
	_ = os.Remove(temporary)
	_ = os.Remove(backup)
	if err := os.WriteFile(temporary, data, 0o755); err != nil {
		return err
	}
	if _, err := os.Stat(destination); err == nil {
		if err := os.Rename(destination, backup); err != nil {
			_ = os.Remove(temporary)
			return errors.New("feche o AI Gateway antes de atualizar")
		}
	}
	if err := os.Rename(temporary, destination); err != nil {
		_ = os.Rename(backup, destination)
		return err
	}
	_ = os.Remove(backup)
	return nil
}

func copySelf(destination string) error {
	executable, err := os.Executable()
	if err != nil {
		return err
	}
	data, err := os.ReadFile(executable)
	if err != nil {
		return err
	}
	return replaceFile(destination, data)
}

func createStartMenuShortcut(directory, application string) error {
	appData := os.Getenv("APPDATA")
	if appData == "" {
		return errors.New("APPDATA não está definido")
	}
	shortcut := filepath.Join(appData, "Microsoft", "Windows", "Start Menu", "Programs", "AI Gateway.lnk")
	script := `$shell = New-Object -ComObject WScript.Shell; $link = $shell.CreateShortcut($env:AIGW_SHORTCUT); $link.TargetPath = $env:AIGW_APP; $link.WorkingDirectory = $env:AIGW_DIR; $link.Save()`
	cmd := exec.Command("powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script)
	cmd.SysProcAttr = &syscall.SysProcAttr{HideWindow: true}
	cmd.Env = append(os.Environ(), "AIGW_SHORTCUT="+shortcut, "AIGW_APP="+application, "AIGW_DIR="+directory)
	if output, err := cmd.CombinedOutput(); err != nil {
		return fmt.Errorf("criar atalho: %s", strings.TrimSpace(string(output)))
	}
	return nil
}

func removeStartMenuShortcut() error {
	shortcut := filepath.Join(os.Getenv("APPDATA"), "Microsoft", "Windows", "Start Menu", "Programs", "AI Gateway.lnk")
	if err := os.Remove(shortcut); err != nil && !errors.Is(err, os.ErrNotExist) {
		return err
	}
	return nil
}

func registerUninstaller(directory, application, uninstaller string) error {
	key := `HKCU\Software\Microsoft\Windows\CurrentVersion\Uninstall\AIGateway`
	values := [][3]string{
		{"DisplayName", "REG_SZ", productName},
		{"DisplayVersion", "REG_SZ", "1.0.0"},
		{"Publisher", "REG_SZ", "Bilius10"},
		{"InstallLocation", "REG_SZ", directory},
		{"DisplayIcon", "REG_SZ", application},
		{"UninstallString", "REG_SZ", strconv.Quote(uninstaller) + " --uninstall"},
	}
	for _, value := range values {
		if err := hiddenRun("reg.exe", "add", key, "/v", value[0], "/t", value[1], "/d", value[2], "/f"); err != nil {
			return err
		}
	}
	return nil
}

func hiddenRun(name string, args ...string) error {
	cmd := exec.Command(name, args...)
	cmd.SysProcAttr = &syscall.SysProcAttr{HideWindow: true}
	if output, err := cmd.CombinedOutput(); err != nil {
		return fmt.Errorf("%s: %s", name, strings.TrimSpace(string(output)))
	}
	return nil
}

func scheduleSelfRemoval(directory string) {
	script := `Start-Sleep -Seconds 2; Remove-Item -LiteralPath $env:AIGW_UNINSTALLER -Force -ErrorAction SilentlyContinue; Remove-Item -LiteralPath $env:AIGW_DIR -Force -ErrorAction SilentlyContinue`
	cmd := exec.Command("powershell.exe", "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden", "-Command", script)
	cmd.SysProcAttr = &syscall.SysProcAttr{HideWindow: true}
	cmd.Env = append(os.Environ(), "AIGW_UNINSTALLER="+filepath.Join(directory, "Uninstall AI Gateway.exe"), "AIGW_DIR="+directory)
	_ = cmd.Start()
}

func hasArg(expected string) bool {
	for _, argument := range os.Args[1:] {
		if argument == expected {
			return true
		}
	}
	return false
}

func confirm(message string) bool { return messageBox(message, 0x00000004|0x00000020) == 6 }
func showInfo(message string)     { messageBox(message, 0x00000040) }
func showError(message string)    { messageBox(message, 0x00000010) }

func messageBox(message string, flags uintptr) int {
	user32 := syscall.NewLazyDLL("user32.dll")
	procedure := user32.NewProc("MessageBoxW")
	text, _ := syscall.UTF16PtrFromString(message)
	title, _ := syscall.UTF16PtrFromString(productName)
	result, _, _ := procedure.Call(0, uintptr(unsafe.Pointer(text)), uintptr(unsafe.Pointer(title)), flags)
	return int(result)
}
