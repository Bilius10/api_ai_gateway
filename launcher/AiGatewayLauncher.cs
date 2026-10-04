using System;
using System.Diagnostics;
using System.IO;
using System.Text;

internal static class Program
{
    private static int Main(string[] args)
    {
        Console.Title = "AI Gateway Launcher";
        bool checkOnly = args.Length > 0 && args[0] == "--check";

        string projectRoot = TrimTrailingDirectorySeparator(Path.GetFullPath(AppDomain.CurrentDomain.BaseDirectory));
        string backendProject = Path.Combine(projectRoot, "backend", "pyproject.toml");
        string frontendProject = Path.Combine(projectRoot, "frontend", "package.json");

        if (!File.Exists(backendProject) || !File.Exists(frontendProject))
        {
            Console.Error.WriteLine("Execute este arquivo a partir da raiz do projeto AI Gateway.");
            Console.Error.WriteLine("Arquivos esperados: backend/pyproject.toml e frontend/package.json.");
            Pause();
            return 1;
        }

        string powershell = FindCommand("pwsh.exe");
        if (string.IsNullOrWhiteSpace(powershell))
        {
            powershell = FindCommand("powershell.exe");
        }

        if (string.IsNullOrWhiteSpace(powershell))
        {
            Console.Error.WriteLine("PowerShell nao foi encontrado.");
            Pause();
            return 1;
        }

        string launcherDir = Path.Combine(projectRoot, ".cache", "launcher");
        Directory.CreateDirectory(launcherDir);

        string backendScript = Path.Combine(launcherDir, "backend.ps1");
        string frontendScript = Path.Combine(launcherDir, "frontend.ps1");

        WriteUtf8File(backendScript, BackendScript(projectRoot));
        WriteUtf8File(frontendScript, FrontendScript(projectRoot));

        if (checkOnly)
        {
        Console.WriteLine("Launcher OK.");
        Console.WriteLine("Projeto: " + projectRoot);
        Console.WriteLine("PowerShell: " + powershell);
        Console.WriteLine("Python 3.12+: " + (HasPython312() ? "OK" : "NAO ENCONTRADO"));
        Console.WriteLine("Node/npm: " + (HasCommand("node.exe") && HasCommand("npm.cmd") ? "OK" : "NAO ENCONTRADO"));
        Console.WriteLine("App desktop: " + (!string.IsNullOrWhiteSpace(FindAppBrowser()) ? "OK" : "EDGE/CHROME NAO ENCONTRADO"));
        return 0;
        }

        StartTerminal("AI Gateway Backend", powershell, backendScript);
        StartTerminal("AI Gateway Frontend", powershell, frontendScript);
        StartAppWindow(projectRoot);

        Console.WriteLine("Comandos de inicializacao enviados.");
        Console.WriteLine("Backend/API: http://127.0.0.1:8000");
        Console.WriteLine("Backend/API externo: http://<ip-da-maquina>:8000");
        Console.WriteLine("App: janela desktop local");
        Console.WriteLine();
        Console.WriteLine("Aguarde os dois terminais terminarem de subir e acesse o frontend.");
        Pause();
        return 0;
    }

    private static string BackendScript(string root)
    {
        return @"$ErrorActionPreference = 'Stop'

$ProjectRoot = " + QuotePowerShell(root) + @"
$BackendRoot = Join-Path $ProjectRoot 'backend'
$CacheRoot = Join-Path $ProjectRoot '.cache'
$VenvRoot = Join-Path $BackendRoot '.venv'

$env:UV_CACHE_DIR = Join-Path $CacheRoot 'uv'
$env:npm_config_cache = Join-Path $CacheRoot 'npm'
$env:VIRTUAL_ENV = $VenvRoot

function Invoke-CommandArray {
    param([string[]]$Command)
    if ($Command.Count -eq 0) {
        throw 'Comando vazio.'
    }

    if ($Command.Count -eq 1) {
        & $Command[0]
    } else {
        & $Command[0] @($Command[1..($Command.Count - 1)])
    }
}

function Test-Python312 {
    param([string]$Exe, [string[]]$PrefixArgs)
    try {
        & $Exe @PrefixArgs -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 12) else 1)' *> $null
        return $LASTEXITCODE -eq 0
    } catch {
        return $false
    }
}

function Test-PortOpen {
    param([int]$Port)
    try {
        $Client = [System.Net.Sockets.TcpClient]::new()
        $Connected = $Client.ConnectAsync('127.0.0.1', $Port).Wait(500)
        $Client.Dispose()
        return $Connected
    } catch {
        return $false
    }
}

function Resolve-Python312 {
    if (Get-Command py -ErrorAction SilentlyContinue) {
        if (Test-Python312 'py' @('-3.12')) {
            return @{ Exe = 'py'; Args = @('-3.12') }
        }
    }

    foreach ($Candidate in @('python3.12', 'python3', 'python')) {
        if (Get-Command $Candidate -ErrorAction SilentlyContinue) {
            if (Test-Python312 $Candidate @()) {
                return @{ Exe = $Candidate; Args = @() }
            }
        }
    }

    throw 'Python 3.12+ nao encontrado. Instale Python 3.12+ no Windows.'
}

New-Item -ItemType Directory -Force -Path $CacheRoot | Out-Null
Set-Location $BackendRoot

if (Test-PortOpen 8000) {
    Write-Host 'Backend ja esta rodando em http://127.0.0.1:8000'
    Write-Host 'Feche o terminal antigo se quiser reiniciar o backend.'
    return
}

if (Get-Command uv -ErrorAction SilentlyContinue) {
    & uv sync --locked --all-groups
    if ($LASTEXITCODE -ne 0) { throw 'uv sync falhou.' }
    $PythonCommand = @('uv', 'run', 'python')
    $BackendCommand = @('uv', 'run', 'uvicorn', 'ai_gateway.app:app', '--host', '0.0.0.0', '--port', '8000', '--reload')
} else {
    $Python = Resolve-Python312

    if (!(Test-Path (Join-Path $VenvRoot 'Scripts\python.exe'))) {
        & $Python.Exe @($Python.Args + @('-m', 'venv', $VenvRoot))
        if ($LASTEXITCODE -ne 0) { throw 'Falha ao criar .venv do backend.' }
    }

    $VenvPython = Join-Path $VenvRoot 'Scripts\python.exe'
    & $VenvPython -m pip install --upgrade pip
    if ($LASTEXITCODE -ne 0) { throw 'Falha ao atualizar pip.' }
    & $VenvPython -m pip install -e '.[dev]'
    if ($LASTEXITCODE -ne 0) { throw 'Falha ao instalar dependencias Python.' }

    $PythonCommand = @($VenvPython)
    $BackendCommand = @($VenvPython, '-m', 'uvicorn', 'ai_gateway.app:app', '--host', '0.0.0.0', '--port', '8000', '--reload')
}

if ([string]::IsNullOrWhiteSpace($env:AI_GATEWAY_MASTER_KEY)) {
    $KeyFile = Join-Path $CacheRoot 'ai-gateway-master-key.env'
    if (Test-Path $KeyFile) {
        Get-Content $KeyFile | ForEach-Object {
            if ($_ -match '^AI_GATEWAY_MASTER_KEY=(.+)$') {
                $env:AI_GATEWAY_MASTER_KEY = $Matches[1]
            }
        }
    } else {
        $GeneratedKey = Invoke-CommandArray @($PythonCommand + @('-c', 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'))
        if ($LASTEXITCODE -ne 0) { throw 'Falha ao gerar AI_GATEWAY_MASTER_KEY.' }
        Set-Content -Path $KeyFile -Value ('AI_GATEWAY_MASTER_KEY=' + $GeneratedKey) -Encoding utf8
        $env:AI_GATEWAY_MASTER_KEY = $GeneratedKey
    }
}

Invoke-CommandArray $BackendCommand
";
    }

    private static string FrontendScript(string root)
    {
        return @"$ErrorActionPreference = 'Stop'

$ProjectRoot = " + QuotePowerShell(root) + @"
$FrontendRoot = Join-Path $ProjectRoot 'frontend'
$CacheRoot = Join-Path $ProjectRoot '.cache'

$env:npm_config_cache = Join-Path $CacheRoot 'npm'

function Test-PortOpen {
    param([int]$Port)
    try {
        $Client = [System.Net.Sockets.TcpClient]::new()
        $Connected = $Client.ConnectAsync('127.0.0.1', $Port).Wait(500)
        $Client.Dispose()
        return $Connected
    } catch {
        return $false
    }
}

if (!(Get-Command node -ErrorAction SilentlyContinue)) {
    throw 'Node.js nao encontrado. Instale Node.js 22+ no Windows.'
}

if (!(Get-Command npm -ErrorAction SilentlyContinue)) {
    throw 'npm nao encontrado. Instale Node.js 22+ no Windows.'
}

Set-Location $FrontendRoot

if (Test-PortOpen 4200) {
    Write-Host 'Frontend ja esta rodando em http://127.0.0.1:4200'
    Write-Host 'Feche o terminal antigo se quiser reiniciar o frontend.'
    return
}

if (Test-Path 'package-lock.json') {
    & npm ci
} else {
    & npm install
}

if ($LASTEXITCODE -ne 0) {
    throw 'Falha ao instalar dependencias do frontend.'
}

& npm start -- --host 127.0.0.1 --port 4200
";
    }

    private static void WriteUtf8File(string path, string content)
    {
        File.WriteAllText(path, content.Replace("\r\n", "\n"), new UTF8Encoding(false));
    }

    private static void StartTerminal(string title, string powershell, string scriptPath)
    {
        ProcessStartInfo info = new ProcessStartInfo();
        info.FileName = "cmd.exe";
        info.Arguments = "/k title " + title + " & " + QuoteCmdArgument(powershell) + " -NoProfile -ExecutionPolicy Bypass -File " + QuoteCmdArgument(scriptPath);
        info.WorkingDirectory = Environment.GetFolderPath(Environment.SpecialFolder.Windows);
        info.UseShellExecute = true;
        Process.Start(info);
    }

    private static void StartAppWindow(string projectRoot)
    {
        string browser = FindAppBrowser();
        if (string.IsNullOrWhiteSpace(browser))
        {
            Console.WriteLine("Edge/Chrome nao encontrado. Abra http://127.0.0.1:4200 manualmente.");
            return;
        }

        string userDataDir = Path.Combine(projectRoot, ".cache", "app-browser");
        Directory.CreateDirectory(userDataDir);

        ProcessStartInfo info = new ProcessStartInfo();
        info.FileName = browser;
        info.Arguments = "--app=http://127.0.0.1:4200 --new-window --user-data-dir=" + QuoteCmdArgument(userDataDir);
        info.WorkingDirectory = projectRoot;
        info.UseShellExecute = true;
        Process.Start(info);
    }

    private static string FindAppBrowser()
    {
        string edge = FindCommand("msedge.exe");
        if (!string.IsNullOrWhiteSpace(edge))
        {
            return edge;
        }

        string[] edgeCandidates = new[]
        {
            Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.ProgramFilesX86), "Microsoft", "Edge", "Application", "msedge.exe"),
            Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.ProgramFiles), "Microsoft", "Edge", "Application", "msedge.exe"),
        };

        foreach (string candidate in edgeCandidates)
        {
            if (File.Exists(candidate))
            {
                return candidate;
            }
        }

        string chrome = FindCommand("chrome.exe");
        if (!string.IsNullOrWhiteSpace(chrome))
        {
            return chrome;
        }

        string[] chromeCandidates = new[]
        {
            Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.ProgramFiles), "Google", "Chrome", "Application", "chrome.exe"),
            Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.ProgramFilesX86), "Google", "Chrome", "Application", "chrome.exe"),
        };

        foreach (string candidate in chromeCandidates)
        {
            if (File.Exists(candidate))
            {
                return candidate;
            }
        }

        return "";
    }

    private static bool HasCommand(string command)
    {
        return !string.IsNullOrWhiteSpace(FindCommand(command));
    }

    private static string FindCommand(string command)
    {
        try
        {
            ProcessStartInfo info = new ProcessStartInfo();
            info.FileName = "where.exe";
            info.Arguments = command;
            info.UseShellExecute = false;
            info.CreateNoWindow = true;
            info.RedirectStandardOutput = true;
            info.RedirectStandardError = true;

            using (Process process = Process.Start(info))
            {
                string output = process.StandardOutput.ReadLine();
                process.WaitForExit();
                return process.ExitCode == 0 ? output : "";
            }
        }
        catch
        {
            return "";
        }
    }

    private static bool HasPython312()
    {
        return TestCommand("py", "-3.12 -c \"import sys; raise SystemExit(0 if sys.version_info >= (3, 12) else 1)\"")
            || TestCommand("python3.12", "-c \"import sys; raise SystemExit(0 if sys.version_info >= (3, 12) else 1)\"")
            || TestCommand("python3", "-c \"import sys; raise SystemExit(0 if sys.version_info >= (3, 12) else 1)\"")
            || TestCommand("python", "-c \"import sys; raise SystemExit(0 if sys.version_info >= (3, 12) else 1)\"");
    }

    private static bool TestCommand(string fileName, string arguments)
    {
        try
        {
            ProcessStartInfo info = new ProcessStartInfo();
            info.FileName = fileName;
            info.Arguments = arguments;
            info.UseShellExecute = false;
            info.CreateNoWindow = true;
            info.RedirectStandardOutput = true;
            info.RedirectStandardError = true;

            using (Process process = Process.Start(info))
            {
                process.WaitForExit();
                return process.ExitCode == 0;
            }
        }
        catch
        {
            return false;
        }
    }

    private static string QuotePowerShell(string value)
    {
        return "'" + value.Replace("'", "''") + "'";
    }

    private static string QuoteCmdArgument(string value)
    {
        return "\"" + value.Replace("\"", "") + "\"";
    }

    private static string TrimTrailingDirectorySeparator(string path)
    {
        string root = Path.GetPathRoot(path);
        while (path.Length > root.Length && (path.EndsWith("\\") || path.EndsWith("/")))
        {
            path = path.Substring(0, path.Length - 1);
        }

        return path;
    }

    private static void Pause()
    {
        Console.WriteLine("Pressione qualquer tecla para fechar esta janela.");
        Console.ReadKey(true);
    }
}
