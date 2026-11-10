param([Parameter(Mandatory=$true)][string]$ReportPath)
$ErrorActionPreference = 'Stop'
$wordApp = $null
$reportDoc = $null
try {
    $wordApp = New-Object -ComObject Word.Application
    $wordApp.Visible = $false
    $wordApp.DisplayAlerts = 0
    $reportDoc = $wordApp.Documents.Open($ReportPath, $false, $false, $false)
    if ($reportDoc.ReadOnly) { throw 'The report is open read-only; close the existing Word window before updating.' }
    if ($reportDoc.TablesOfContents.Count -ne 1) { throw 'Expected exactly one automatic table of contents.' }
    $reportDoc.Repaginate()
    $reportDoc.TablesOfContents.Item(1).Update()
    $reportDoc.Repaginate()
    $reportDoc.TablesOfContents.Item(1).UpdatePageNumbers()
    $reportDoc.Repaginate()
    $bodyRange = $reportDoc.Content.Duplicate
    $found = $bodyRange.Find.Execute('1. Introduction and requirements')
    # First match is in the newly populated TOC; locate the actual Heading 1.
    $bodyPage = $null
    foreach ($paragraph in $reportDoc.Paragraphs) {
        if ($paragraph.Range.Text.Trim() -eq '1. Introduction and requirements' -and $paragraph.OutlineLevel -eq 1) {
            $bodyPage = $paragraph.Range.Information(1)
            break
        }
    }
    if ($bodyPage -ne 1) { throw "Body numbering check failed: found page $bodyPage" }
    $reportDoc.Save()
    $result = [ordered]@{
        updatedBy = 'Microsoft Word'
        physicalPages = $reportDoc.ComputeStatistics(2)
        sections = $reportDoc.Sections.Count
        bodyFirstPage = $bodyPage
        automaticTocCount = $reportDoc.TablesOfContents.Count
        tocText = $reportDoc.TablesOfContents.Item(1).Range.Text
        pdfGenerated = $false
    }
    $resultPath = Join-Path (Split-Path $ReportPath) 'word-pagination-check.json'
    $result | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $resultPath -Encoding UTF8
    $result | ConvertTo-Json -Depth 4
} finally {
    if ($reportDoc) { $reportDoc.Close(0); [void][Runtime.InteropServices.Marshal]::ReleaseComObject($reportDoc) }
    if ($wordApp) { $wordApp.Quit(); [void][Runtime.InteropServices.Marshal]::ReleaseComObject($wordApp) }
}
