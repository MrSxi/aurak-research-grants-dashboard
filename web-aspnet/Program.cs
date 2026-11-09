// AURAK Research Grants Dashboard: ASP.NET Core web frontend.
//
// Architecture (per the AURAK project guidance: ASP.NET web frontend, Microsoft SQL Server database):
//   Browser -> ASP.NET Core (this app, http://localhost:8000)
//              - serves the AURAK stylesheet and other static files itself
//              - hosts the public web endpoint, health checks and request routing
//              - forwards page and API requests to the Python service (YARP reverse proxy)
//   Python service (FastAPI, http://127.0.0.1:8100, internal only)
//              - page rendering, grant matching, AI assistant (RAG), scheduled grant refresh
//   Microsoft SQL Server Express (database "aurak")
//
// The pages users see are exactly the same as before; this layer changes how the site is hosted, not how it looks.
using System.Net.Http.Json;
using System.Text.Encodings.Web;
using AurakWeb.Infrastructure;
using Microsoft.Extensions.FileProviders;
using Yarp.ReverseProxy.Transforms;

var builder = WebApplication.CreateBuilder(args);

var projectRoot = Path.GetFullPath(Path.Combine(builder.Environment.ContentRootPath, ".."));
var backendUrl = builder.Configuration["Backend:Url"] ?? "http://127.0.0.1:8100";

builder.Services.AddReverseProxy()
    .LoadFromConfig(builder.Configuration.GetSection("ReverseProxy"))
    .AddTransforms(ctx =>
    {
        // Keep the original Host header so the backend builds links and cookies for localhost:8000
        ctx.AddOriginalHost(true);
    });
builder.Services.AddHttpClient("backend", c =>
{
    c.BaseAddress = new Uri(backendUrl);
    c.Timeout = TimeSpan.FromSeconds(5);
});

// Pages are rendered here with Razor views (Views/Pages); the backend supplies each page's data.
// The encoder escapes exactly like the original pages (& < > " ' only), so text appears unchanged.
builder.Services.AddSingleton<HtmlEncoder>(new MarkupSafeEncoder());
builder.Services.AddControllersWithViews();
builder.Services.AddSingleton<PageViews>();
builder.Services.AddHttpClient("pages", c =>
{
    c.BaseAddress = new Uri(backendUrl);
    c.Timeout = TimeSpan.FromMinutes(5);
}).ConfigurePrimaryHttpMessageHandler(() => new HttpClientHandler
{
    AllowAutoRedirect = false,  // redirects go back to the browser as they are
    UseCookies = false,         // the browser's own cookie header is forwarded
});

var app = builder.Build();

// Static files (AURAK stylesheet) are served by ASP.NET Core straight from the shared app/static folder
var staticDir = Path.Combine(projectRoot, "app", "static");
if (Directory.Exists(staticDir))
{
    app.UseStaticFiles(new StaticFileOptions
    {
        FileProvider = new PhysicalFileProvider(staticDir),
        RequestPath = "/static",
    });
}
// Routing comes after static files, so the catch-all proxy route does not claim /static requests
app.UseRouting();

// Health of the whole stack (frontend, backend service, database) for IT monitoring
app.MapGet("/aspnet/health", async (IHttpClientFactory http) =>
{
    var backend = "unreachable";
    object? backendInfo = null;
    try
    {
        backendInfo = await http.CreateClient("backend").GetFromJsonAsync<Dictionary<string, object>>("/healthz");
        backend = "ok";
    }
    catch (Exception)
    {
        // reported below
    }
    var status = backend == "ok" ? StatusCodes.Status200OK : StatusCodes.Status503ServiceUnavailable;
    return Results.Json(new
    {
        frontend = "ASP.NET Core " + Environment.Version,
        backend,
        backendInfo,
        database = "Microsoft SQL Server (configured in .env)",
    }, statusCode: status);
});

// Pages (GET) are rendered by ASP.NET Core with Razor
app.MapControllers();

// Form posts and the streaming assistant API go to the Python service
app.MapReverseProxy();

app.Run();
