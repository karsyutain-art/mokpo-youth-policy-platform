param(
    [Parameter(Mandatory = $true)][string]$InputPath,
    [Parameter(Mandatory = $true)][string]$OutputPath
)

Add-Type -AssemblyName System.IO.Compression.FileSystem

function Get-EntryText([System.IO.Compression.ZipArchive]$Zip, [string]$Name) {
    $entry = $Zip.GetEntry($Name)
    if ($null -eq $entry) { throw "HWPX entry not found: $Name" }
    $reader = [System.IO.StreamReader]::new($entry.Open(), [System.Text.Encoding]::UTF8, $true)
    try { return $reader.ReadToEnd() } finally { $reader.Dispose() }
}

function Get-Text($Node) {
    $parts = [System.Collections.Generic.List[string]]::new()
    $walk = {
        param($item)
        foreach ($child in $item.ChildNodes) {
            if ($child.NodeType -ne [System.Xml.XmlNodeType]::Element) { continue }
            if ($child.LocalName -in @('header', 'footer', 'tbl')) { continue }
            if ($child.LocalName -eq 't') { $parts.Add($child.InnerText); continue }
            & $walk $child
        }
    }
    & $walk $Node
    return (($parts -join '') -replace "`r?`n", ' ' -replace '\s+', ' ').Trim()
}

function Escape-Cell([string]$Value) {
    return (($Value -replace '\|', '\|') -replace "`r?`n", '<br>')
}

function Write-Table($Table, [System.Text.StringBuilder]$Out) {
    $rows = @($Table.SelectNodes('./*[local-name()="tr"]'))
    if ($rows.Count -eq 0) { return }
    $rendered = foreach ($row in $rows) {
        $cells = @($row.SelectNodes('./*[local-name()="tc"]'))
        @($cells | ForEach-Object { Escape-Cell (Get-Text $_) })
    }
    $width = ($rendered | ForEach-Object { $_.Count } | Measure-Object -Maximum).Maximum
    if (-not $width) { return }
    foreach ($row in $rendered) {
        $values = @($row)
        while ($values.Count -lt $width) { $values += '' }
        [void]$Out.AppendLine('| ' + ($values -join ' | ') + ' |')
        if ($row -eq $rendered[0]) { [void]$Out.AppendLine('| ' + ((1..$width | ForEach-Object { '---' }) -join ' | ') + ' |') }
    }
    [void]$Out.AppendLine()
}

$zip = [System.IO.Compression.ZipFile]::OpenRead($InputPath)
try {
    [xml]$header = Get-EntryText $zip 'Contents/header.xml'
    $styleNames = @{}
    foreach ($style in $header.SelectNodes('//*[local-name()="style"]')) {
        if ($style.id -and $style.name) { $styleNames[[string]$style.id] = [string]$style.name }
    }

    [xml]$section = Get-EntryText $zip 'Contents/section0.xml'
    $out = [System.Text.StringBuilder]::new()
    $titleWritten = $false
    $assetDir = Join-Path (Split-Path -Parent $OutputPath) (([System.IO.Path]::GetFileNameWithoutExtension($OutputPath)) + '_assets')
    $children = @($section.DocumentElement.ChildNodes | Where-Object { $_.LocalName -eq 'p' })
    foreach ($p in $children) {
        $tables = @($p.SelectNodes('./*[local-name()="run"]/*[local-name()="tbl"]'))
        if ($tables.Count -gt 0) {
            foreach ($table in $tables) { Write-Table $table $out }
            continue
        }
        $imageRefs = @($p.SelectNodes('.//*[local-name()="img"]'))
        if ($imageRefs.Count -gt 0) {
            if (-not (Test-Path -LiteralPath $assetDir)) { New-Item -ItemType Directory -Path $assetDir | Out-Null }
            foreach ($image in $imageRefs) {
                $id = [string]$image.binaryItemIDRef
                $entry = @($zip.Entries | Where-Object { $_.FullName -match ('^BinData/' + [regex]::Escape($id) + '\.') }) | Select-Object -First 1
                if ($entry) {
                    $destination = Join-Path $assetDir ([System.IO.Path]::GetFileName($entry.FullName))
                    $source = $entry.Open(); $target = [System.IO.File]::Create($destination)
                    try { $source.CopyTo($target) } finally { $target.Dispose(); $source.Dispose() }
                    $relativeAsset = (Split-Path -Leaf $assetDir) + '/' + [System.IO.Path]::GetFileName($entry.FullName)
                    [void]$out.AppendLine('![첨부 이미지](' + $relativeAsset + ')'); [void]$out.AppendLine()
                }
            }
        }
        $value = Get-Text $p
        if ([string]::IsNullOrWhiteSpace($value)) { continue }
        $style = $styleNames[[string]$p.styleIDRef]
        if (-not $titleWritten) {
            [void]$out.AppendLine('# ' + $value); [void]$out.AppendLine(); $titleWritten = $true; continue
        }
        if ($style -match '(제목|heading|개요|목차|Title)' -or $value -match '^(프로젝트 개요|개요|요구사항|유스케이스|기능 요구사항|비기능 요구사항|화면 요구사항)') {
            [void]$out.AppendLine('## ' + $value); [void]$out.AppendLine()
        } else {
            [void]$out.AppendLine($value); [void]$out.AppendLine()
        }
    }
    if ($out.Length -eq 0) { throw 'No document content could be extracted.' }
    $markdown = $out.ToString().TrimEnd() + "`r`n"
    # HWPX templates commonly place the document title in a one-cell table.
    if ($markdown -match '^\| ([^|\r\n]+) \|\r?\n\r?\n') {
        $markdown = [regex]::Replace($markdown, '^\| ([^|\r\n]+) \|\r?\n\r?\n', '# $1' + "`r`n`r`n", 1)
    }
    # With a table-based title promoted above, later document-level headings
    # should become second-level headings rather than competing titles.
    $firstHeading = $true
    $mainTitle = ''
    $normalizedLines = foreach ($line in ($markdown -split "`r?`n")) {
        if ($line -match '^# ') {
            if ($firstHeading) { $firstHeading = $false; $mainTitle = $line.Substring(2); $line }
            elseif ($line.Substring(2) -eq $mainTitle) { continue }
            else { '## ' + $line.Substring(2) }
        } else { $line }
    }
    $markdown = ($normalizedLines -join "`r`n").TrimEnd() + "`r`n"
    [System.IO.File]::WriteAllText($OutputPath, $markdown, [System.Text.UTF8Encoding]::new($false))
} finally {
    $zip.Dispose()
}
