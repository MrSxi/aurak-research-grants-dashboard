using System.Text;
using System.Text.Encodings.Web;

namespace AurakWeb.Infrastructure;

/// <summary>
/// HTML encoder for the Razor views that escapes exactly what the original pages escaped (&amp; &lt; &gt; " ')
/// and leaves all other characters (Arabic, accents, symbols) as they are.
/// </summary>
public sealed class MarkupSafeEncoder : HtmlEncoder
{
    public override int MaxOutputCharactersPerInputCharacter => 5;

    public static string Escape(string s)
    {
        if (s.IndexOfAny(new[] { '&', '<', '>', '"', '\'' }) < 0) return s;
        var sb = new StringBuilder(s.Length + 16);
        foreach (var c in s)
            sb.Append(c switch { '&' => "&amp;", '<' => "&lt;", '>' => "&gt;", '"' => "&#34;", '\'' => "&#39;", _ => c.ToString() });
        return sb.ToString();
    }

    private static string? Replacement(int scalar) => scalar switch
    {
        '&' => "&amp;",
        '<' => "&lt;",
        '>' => "&gt;",
        '"' => "&#34;",
        '\'' => "&#39;",
        _ => null,
    };

    public override bool WillEncode(int unicodeScalar) => Replacement(unicodeScalar) is not null;

    public override unsafe int FindFirstCharacterToEncode(char* text, int textLength)
    {
        for (int i = 0; i < textLength; i++)
            if (WillEncode(text[i])) return i;
        return -1;
    }

    public override unsafe bool TryEncodeUnicodeScalar(int unicodeScalar, char* buffer, int bufferLength, out int numberOfCharactersWritten)
    {
        var rep = Replacement(unicodeScalar);
        if (rep is null)
        {
            var s = char.ConvertFromUtf32(unicodeScalar);
            if (s.Length > bufferLength) { numberOfCharactersWritten = 0; return false; }
            for (int i = 0; i < s.Length; i++) buffer[i] = s[i];
            numberOfCharactersWritten = s.Length;
            return true;
        }
        if (rep.Length > bufferLength) { numberOfCharactersWritten = 0; return false; }
        for (int i = 0; i < rep.Length; i++) buffer[i] = rep[i];
        numberOfCharactersWritten = rep.Length;
        return true;
    }
}
