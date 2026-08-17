param(
    [Parameter(Mandatory = $true)]
    [string]$FlowUrl
)

# Keep the source ASCII-only so Windows PowerShell 5.1 cannot misread the
# script encoding. ConvertFrom-Json resolves the Unicode escapes correctly.
$payloadJson = @'
{
  "window_id": "WIN-TEST",
  "primary_clause_ids": ["CLAUSE-0001"],
  "clauses": [
    {
      "clause_id": "CLAUSE-0001",
      "speaker": "Linh",
      "text": "Em nh\u1eadn ki\u1ec3m tra quy\u1ec1n truy c\u1eadp.",
      "flags": ["FIRST_PERSON_COMMITMENT"]
    }
  ],
  "date_mentions": []
}
'@

$payload = $payloadJson | ConvertFrom-Json
$jsonBody = $payload | ConvertTo-Json -Depth 10 -Compress
$utf8Body = [System.Text.UTF8Encoding]::new($false).GetBytes($jsonBody)

$response = Invoke-RestMethod `
    -Uri $FlowUrl `
    -Method Post `
    -ContentType "application/json; charset=utf-8" `
    -Body $utf8Body

$response | ConvertTo-Json -Depth 10
