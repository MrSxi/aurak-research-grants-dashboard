using System.Text.Json.Nodes;
using Microsoft.AspNetCore.Mvc;
using Microsoft.AspNetCore.Mvc.Rendering;
using Microsoft.AspNetCore.Mvc.ViewEngines;

namespace AurakWeb.Infrastructure;

/// <summary>
/// Serves every page (GET). Asks the Python backend for the page's view-model and renders it with the Razor view
/// of the same name (Views/Pages/...). Anything that is not a page (redirects, JSON, files) is passed on as is.
/// </summary>
public sealed class PageController : Controller
{
    public const string PageKey = "aurak.page";
    private const string PageMediaType = "application/vnd.aurak.page+json";
    private static readonly string[] ForwardRequestHeaders = { "Cookie", "Accept", "Accept-Language", "User-Agent", "Referer" };
    private static readonly HashSet<string> SkipResponseHeaders = new(StringComparer.OrdinalIgnoreCase)
        { "Transfer-Encoding", "Content-Length", "Connection", "Server", "Date", "Keep-Alive" };

    private readonly IHttpClientFactory _http;
    private readonly PageViews _views;

    public PageController(IHttpClientFactory http, PageViews views)
    {
        _http = http;
        _views = views;
    }

    [HttpGet("{**path}")]
    public async Task Get(string? path)
    {
        var client = _http.CreateClient("pages");
        using var req = new HttpRequestMessage(HttpMethod.Get, Request.Path + Request.QueryString);
        foreach (var h in ForwardRequestHeaders)
            if (Request.Headers.TryGetValue(h, out var v)) req.Headers.TryAddWithoutValidation(h, v.ToArray());
        req.Headers.Host = Request.Host.Value;  // links and cookies stay on localhost:8000
        req.Headers.Add("X-Render-As", "json");
        req.Headers.Add("X-Render-Templates", _views.TemplateList);

        using var resp = await client.SendAsync(req, HttpCompletionOption.ResponseHeadersRead, HttpContext.RequestAborted);
        Response.StatusCode = (int)resp.StatusCode;
        foreach (var h in resp.Headers.Concat(resp.Content.Headers))
        {
            if (SkipResponseHeaders.Contains(h.Key) || h.Key.Equals("Content-Type", StringComparison.OrdinalIgnoreCase)) continue;
            Response.Headers[h.Key] = h.Value.ToArray();
        }

        if (resp.Content.Headers.ContentType?.MediaType == PageMediaType)
        {
            var doc = JsonNode.Parse(await resp.Content.ReadAsStringAsync())!;
            var template = (string)doc["template"]!;
            HttpContext.Items[PageKey] = new J(doc["model"]);
            var view = _views.Find(template) ?? throw new InvalidOperationException($"No Razor view for {template}");
            Response.ContentType = "text/html; charset=utf-8";
            Response.Headers["X-Rendered-By"] = "ASP.NET Razor (" + template + ")";
            await RenderAsync(view);
            return;
        }

        if (resp.Content.Headers.ContentType is { } ct) Response.ContentType = ct.ToString();
        await resp.Content.CopyToAsync(Response.Body, HttpContext.RequestAborted);
    }

    private async Task RenderAsync(IView view)
    {
        ViewData.Model = HttpContext.Items[PageKey];
        await using var writer = new StreamWriter(Response.Body, new System.Text.UTF8Encoding(false), leaveOpen: true);
        var ctx = new ViewContext(ControllerContext, view, ViewData, TempData, writer, new Microsoft.AspNetCore.Mvc.ViewFeatures.HtmlHelperOptions());
        await view.RenderAsync(ctx);
        await writer.FlushAsync();
    }
}

/// <summary>Finds the Razor view for a backend template name, e.g. "admin/queue.html" -> Views/Pages/admin/queue.cshtml.</summary>
public sealed class PageViews
{
    private readonly ICompositeViewEngine _engine;
    public string TemplateList { get; }

    public PageViews(ICompositeViewEngine engine, IWebHostEnvironment env)
    {
        _engine = engine;
        var root = Path.Combine(env.ContentRootPath, "Views", "Pages");
        TemplateList = Directory.Exists(root)
            ? string.Join(",", Directory.EnumerateFiles(root, "*.cshtml", SearchOption.AllDirectories)
                .Select(f => Path.GetRelativePath(root, f).Replace('\\', '/'))
                .Where(f => !Path.GetFileName(f).StartsWith('_'))
                .Select(f => f[..^".cshtml".Length] + ".html"))
            : "";
    }

    public IView? Find(string template)
    {
        var path = "~/Views/Pages/" + template[..^".html".Length] + ".cshtml";
        var r = _engine.GetView(null, path, isMainPage: true);
        return r.Success ? r.View : null;
    }
}
