using System.Net.Http.Json;
using System.Text.Encodings.Web;
using System.Text.Json;

if (args.Length < 4)
{
    Console.Error.WriteLine("Usage: MeetingTaskPipeline.Client <transcript-path> <meeting-id> <meeting-title> <YYYY-MM-DD>");
    return 2;
}

var endpoint = Environment.GetEnvironmentVariable("MEETING_PIPELINE_ENDPOINT")
    ?? "http://127.0.0.1:8000/api/v1/meetings/process";
var apiKey = Environment.GetEnvironmentVariable("MEETING_PIPELINE_API_KEY");
var transcriptPath = Path.GetFullPath(args[0]);

if (!File.Exists(transcriptPath))
{
    Console.Error.WriteLine($"Transcript does not exist: {transcriptPath}");
    return 2;
}

var payload = new
{
    meeting_id = args[1],
    meeting_title = args[2],
    meeting_date = args[3],
    file_name = Path.GetFileName(transcriptPath),
    transcript = await File.ReadAllTextAsync(transcriptPath),
    speaker_aliases = new Dictionary<string, string>(),
};

using var client = new HttpClient { Timeout = TimeSpan.FromMinutes(5) };
if (!string.IsNullOrWhiteSpace(apiKey))
{
    client.DefaultRequestHeaders.Add("X-API-Key", apiKey);
}

using var response = await client.PostAsJsonAsync(endpoint, payload);
var body = await response.Content.ReadAsStringAsync();
if (!response.IsSuccessStatusCode)
{
    Console.Error.WriteLine($"HTTP {(int)response.StatusCode}: {body}");
    return 1;
}

using var json = JsonDocument.Parse(body);
Console.WriteLine(JsonSerializer.Serialize(
    json.RootElement,
    new JsonSerializerOptions
    {
        WriteIndented = true,
        Encoder = JavaScriptEncoder.UnsafeRelaxedJsonEscaping,
    }));
return 0;
