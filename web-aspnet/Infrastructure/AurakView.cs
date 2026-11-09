using Microsoft.AspNetCore.Html;
using Microsoft.AspNetCore.Mvc.Razor;

namespace AurakWeb.Infrastructure;

/// <summary>Base class for every Razor view: access to the page view-model and the shared helpers.</summary>
public abstract class AurakView<TModel> : RazorPage<TModel>
{
    /// <summary>The whole page view-model from the backend.</summary>
    public J Page => (J)Context.Items[PageController.PageKey]!;

    /// <summary>Page view-model as `dynamic`, e.g. <c>M.grant.title</c>.</summary>
    public dynamic M => Page;

    /// <summary>Shared values (configuration, images, match components).</summary>
    public dynamic G => Page["_globals"];

    public J Config => Page["_globals"]["config"];

    /// <summary>grant_image(id): an AURAK photo for a grant tile, fixed per grant.</summary>
    public string GrantImage(object id)
    {
        var pool = Page["_globals"]["GRANT_POOL"];
        return pool[(int)(J.Of(id).Num % pool.Count)].ToString();
    }

    public string GrantImage(double id) => GrantImage((object)id);

    /// <summary>image_allowed(url): false for photos aurak.ac.ae's robots.txt asks not to use.</summary>
    public bool ImageAllowed(object? url)
    {
        var s = Jinja.Str(url);
        if (string.IsNullOrEmpty(s) || s == "None") return false;
        var path = Uri.TryCreate(s, UriKind.Absolute, out var u) ? u.AbsolutePath : s;
        return !Page["_globals"]["DISALLOWED_IMAGE_PATHS"].Any(p => p.ToString() == path);
    }

    /// <summary>Write already-safe markup without escaping.</summary>
    public IHtmlContent Raw(string html) => new HtmlString(html);
}
