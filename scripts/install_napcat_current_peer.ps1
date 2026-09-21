param(
    [Parameter(Mandatory = $true)]
    [string]$NapCatRoot,

    [switch]$PatchLocalWhitelist
)

$source = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\napcat-plugin-ohmymeme-current-peer'))
$root = [IO.Path]::GetFullPath($NapCatRoot)
$target = [IO.Path]::GetFullPath((Join-Path $root 'plugins\napcat-plugin-ohmymeme-current-peer'))

if (-not (Test-Path -LiteralPath (Join-Path $source 'package.json'))) {
    throw "插件源目录无效：$source"
}
if (-not (Test-Path -LiteralPath $root)) {
    throw "NapCat 根目录不存在：$root"
}

$bundle = Join-Path $root 'napcat.mjs'
$bundleNeedsWhitelistPatch = $false
if (Test-Path -LiteralPath $bundle) {
    $bundleText = [IO.File]::ReadAllText($bundle)
    $bundleNeedsWhitelistPatch = (
        $bundleText.Contains('not in official plugin whitelist') -and
        -not $bundleText.Contains('napcat-plugin-ohmymeme-current-peer')
    )
}

if ($bundleNeedsWhitelistPatch -and $PatchLocalWhitelist) {
    $backup = "$bundle.omm-original"
    if (-not (Test-Path -LiteralPath $backup)) {
        Copy-Item -LiteralPath $bundle -Destination $backup -Force
    }
    $pattern = '(?m)(^\s*"napcat-plugin-qce")\r?\n(^\s*\]\);)'
    $replacement = '$1,' + [Environment]::NewLine + '  "napcat-plugin-ohmymeme-current-peer"' + [Environment]::NewLine + '$2'
    $patched = [regex]::Replace($bundleText, $pattern, $replacement, 1)
    if ($patched -eq $bundleText) {
        throw "找不到 NapCat 插件白名单锚点，未修改：$bundle"
    }
    $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    [IO.File]::WriteAllText($bundle, $patched, $utf8NoBom)
    Write-Output "已将 OhMyMeme 插件 ID 加入本地 NapCat 白名单（原件备份：$backup）"
} elseif ($bundleNeedsWhitelistPatch) {
    Write-Warning '当前 NapCat Framework 的插件加载器只接受内置白名单 ID；插件已复制，但重启后可能被拒绝。若确认接受修改本地 napcat.mjs，请重新执行并追加 -PatchLocalWhitelist。'
}

New-Item -ItemType Directory -Force -Path (Split-Path -Parent $target) | Out-Null
New-Item -ItemType Directory -Force -Path $target | Out-Null
Copy-Item -LiteralPath (Join-Path $source 'package.json') -Destination $target -Force
Copy-Item -LiteralPath (Join-Path $source 'index.mjs') -Destination $target -Force

Write-Output "已安装 OhMyMeme 当前会话桥接插件：$target"
Write-Output '请完全重启 NapCat Framework，使插件被扫描并加载。'
