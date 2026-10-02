param(
    [Parameter(Mandatory = $true)]
    [string]$NapCatRoot,

    [switch]$PatchLocalWhitelist
)

$source = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\napcat-plugin-ohmymeme-current-peer'))
$root = [IO.Path]::GetFullPath($NapCatRoot)
$target = [IO.Path]::GetFullPath((Join-Path $root 'plugins\napcat-plugin-ohmymeme-current-peer'))

if (-not (Test-Path -LiteralPath (Join-Path $source 'package.json'))) {
    throw "Invalid plugin source directory: $source"
}
if (-not (Test-Path -LiteralPath $root)) {
    throw "NapCat root directory does not exist: $root"
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
        throw "NapCat plugin whitelist anchor was not found: $bundle"
    }
    $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    [IO.File]::WriteAllText($bundle, $patched, $utf8NoBom)
    Write-Output "Added the OhMyMeme plugin ID to the local NapCat whitelist (backup: $backup)"
} elseif ($bundleNeedsWhitelistPatch) {
    Write-Warning 'This NapCat Framework build may require a local whitelist patch. Re-run with -PatchLocalWhitelist if the copied plugin is rejected after restart.'
}

New-Item -ItemType Directory -Force -Path (Split-Path -Parent $target) | Out-Null
New-Item -ItemType Directory -Force -Path $target | Out-Null
Copy-Item -LiteralPath (Join-Path $source 'package.json') -Destination $target -Force
Copy-Item -LiteralPath (Join-Path $source 'index.mjs') -Destination $target -Force

Write-Output "Installed OhMyMeme current-peer bridge plugin: $target"
Write-Output 'Fully restart NapCat Framework so the plugin is scanned and loaded.'
