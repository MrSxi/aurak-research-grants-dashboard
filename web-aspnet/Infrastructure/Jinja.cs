using System.Globalization;
using System.Text;
using Microsoft.AspNetCore.Html;

namespace AurakWeb.Infrastructure;

/// <summary>
/// The text filters the pages use (date formats, truncation, match scores...), with exactly the output the
/// original Python templates produced, so every page reads the same after the move to ASP.NET.
/// </summary>
public static class Jinja
{
    private static readonly CultureInfo Inv = CultureInfo.InvariantCulture;

    private static J V(object? x) => J.Of(x);

    /// <summary>Text of a value as the original templates printed it (Python str).</summary>
    public static string Str(object? x) => x is string s ? s : V(x).ToString();

    /// <summary>truncate(length): cut long text at a word boundary and add "...", with 5 characters of leeway.</summary>
    public static string Truncate(object? value, int length = 255)
    {
        var s = Str(value);
        var runes = s.EnumerateRunes().ToArray();
        const string end = "...";
        if (runes.Length <= length + 5) return s;
        var head = string.Concat(runes.Take(length - end.Length).Select(r => r.ToString()));
        int sp = head.LastIndexOf(' ');
        if (sp >= 0) head = head[..sp];
        return head + end;
    }

    /// <summary>Match score shown as a whole number out of 100 (Python round: halves go to the even number).</summary>
    public static string Score(object? value) =>
        Math.Round(V(value).Num * 100, MidpointRounding.ToEven).ToString("0", Inv);

    /// <summary>A date like "31 October 2026" (empty when missing).</summary>
    public static string D(object? value)
    {
        var j = V(value);
        return j.Truthy && j.IsDate ? Strf(j, "%d %B %Y") : "";
    }

    /// <summary>Days from today until a date.</summary>
    public static int? DaysLeft(object? value)
    {
        var j = V(value);
        if (!j.Truthy || !j.IsDate) return null;
        return (j.Date.Date - DateTime.Today).Days;
    }

    /// <summary>strftime with the formats the pages use.</summary>
    public static string Strf(object? value, string fmt)
    {
        var j = V(value);
        var dt = j.Date;
        var sb = new StringBuilder();
        for (int i = 0; i < fmt.Length; i++)
        {
            if (fmt[i] != '%' || i + 1 >= fmt.Length) { sb.Append(fmt[i]); continue; }
            char c = fmt[++i];
            sb.Append(c switch
            {
                'd' => dt.Day.ToString("00", Inv),
                'B' => Inv.DateTimeFormat.GetMonthName(dt.Month),
                'b' => Inv.DateTimeFormat.GetAbbreviatedMonthName(dt.Month),
                'Y' => dt.Year.ToString("0000", Inv),
                'H' => dt.Hour.ToString("00", Inv),
                'M' => dt.Minute.ToString("00", Inv),
                'S' => dt.Second.ToString("00", Inv),
                'Z' => j.TzName ?? "",
                '%' => "%",
                _ => "%" + c,
            });
        }
        return sb.ToString();
    }

    /// <summary>ISO date (yyyy-mm-dd) for date inputs.</summary>
    public static string Iso(object? value) => V(value).Date.ToString("yyyy-MM-dd", Inv);

    /// <summary>missing: "Not published" in grey when a value is empty, otherwise the value itself.</summary>
    public static object Missing(object? value, string text = "Not published")
    {
        var j = V(value);
        bool empty = j.IsNone || (j.IsString && j.Raw.Length == 0) || ((j.IsList || j.IsDict) && j.Count == 0);
        if (empty) return new HtmlString("<span class=\"missing\">" + MarkupSafeEncoder.Escape(text) + "</span>");
        return value is string s ? s : j;
    }

    public static string Cap(object? value)
    {
        var s = Str(value);
        return s.Length == 0 ? s : s[..1].ToUpperInvariant() + s[1..].ToLowerInvariant();
    }

    public static string Join(object? list, string sep = "") => string.Join(sep, V(list).Select(x => x.ToString()));

    public static string MatchLabel(object? score, J config) =>
        V(score).Num >= config["MATCH_STRONG"].Num ? "Strong match" : "Possible match";

    /// <summary>'{:,.0f}'.format(n): thousands separators, no decimals.</summary>
    public static string Comma0(object? value) =>
        Math.Round(V(value).Num, MidpointRounding.ToEven).ToString("#,##0", Inv);

    /// <summary>urlencode (keeps "/" like the original)</summary>
    public static string UrlEncode(string s) => Uri.EscapeDataString(s).Replace("%2F", "/");

    /// <summary>|int for numbers</summary>
    public static string Int(object? value) => ((long)Math.Truncate(V(value).Num)).ToString(Inv);

    public static string Plural(long n) => n == 1 ? "" : "s";

    /// <summary>"selected" / "checked" / "open" when a condition holds (Python truthiness), otherwise "".</summary>
    public static string Sel(object? cond) => V(cond).Truthy ? "selected" : "";
    public static string Chk(object? cond) => V(cond).Truthy ? "checked" : "";
    public static string If(object? cond, string yes, string no = "") => V(cond).Truthy ? yes : no;
}
