using System.Collections;
using System.Dynamic;
using System.Globalization;
using System.Text;
using System.Text.Json.Nodes;

namespace AurakWeb.Infrastructure;

/// <summary>
/// A JSON value from the backend's page view-model, with the same behaviour the original templates relied on:
/// Python truthiness, Python-style printing (str / repr), comparisons, indexing and iteration.
/// Views use it through `dynamic`, e.g. <c>@M.grant.title</c>.
/// </summary>
public sealed class J : DynamicObject, IEnumerable<J>
{
    public static readonly J None = new(null);
    private readonly JsonNode? _n;

    public J(JsonNode? node) => _n = node;

    public static J Of(object? v) => v switch
    {
        null => None,
        J j => j,
        JsonNode n => new J(n),
        string s => new J(JsonValue.Create(s)),
        bool b => new J(JsonValue.Create(b)),
        int i => new J(JsonValue.Create(i)),
        long l => new J(JsonValue.Create(l)),
        double d => new J(JsonValue.Create(d)),
        IEnumerable<J> list => new J(new JsonArray(list.Select(x => x._n?.DeepClone()).ToArray())),
        _ => new J(JsonValue.Create(v.ToString())),
    };

    // ------------------------------------------------------------------ kind checks
    public bool IsNone => _n is null;
    public bool IsList => _n is JsonArray;
    public bool IsDict => _n is JsonObject o && !IsDate;
    public bool IsDate => _n is JsonObject o && (o.ContainsKey("$dt") || o.ContainsKey("$d"));
    public bool IsString => _n is JsonValue v && v.TryGetValue<string>(out _);
    public bool IsBool => _n is JsonValue v && v.TryGetValue<bool>(out _);
    public bool IsNumber => _n is JsonValue v && !IsString && !IsBool;

    // ------------------------------------------------------------------ Python truthiness
    public bool Truthy => _n switch
    {
        null => false,
        JsonArray a => a.Count > 0,
        JsonObject o => o.Count > 0,
        JsonValue v when v.TryGetValue<bool>(out var b) => b,
        JsonValue v when v.TryGetValue<string>(out var s) => s.Length > 0,
        _ => Num != 0,
    };

    public static bool operator true(J j) => j.Truthy;
    public static bool operator false(J j) => !j.Truthy;
    // bool -> J (not the other way), so mixed expressions like `M.user && !M.limited` resolve to J's & operator
    public static implicit operator J(bool b) => Of(b);
    public static bool operator !(J j) => !j.Truthy;
    // Python `and` / `or` (return an operand), so `a && b` / `a || b` work on J values
    public static J operator &(J a, J b) => a.Truthy ? b : a;
    public static J operator |(J a, J b) => a.Truthy ? a : b;

    // ------------------------------------------------------------------ values
    public double Num => _n is JsonValue v ? (v.TryGetValue<double>(out var d) ? d : (v.TryGetValue<long>(out var l) ? l : 0)) : 0;
    public long Int => (long)Num;
    public string Raw => _n is JsonValue v && v.TryGetValue<string>(out var s) ? s : ToString();

    public DateTime Date
    {
        get
        {
            var o = (JsonObject)_n!;
            var iso = (string)(o["$dt"] ?? o["$d"])!;
            // wall-clock time exactly as the backend had it (tz-aware values keep their own local time)
            if (iso.Length > 19 && (iso.IndexOf('+', 19) > 0 || iso.IndexOf('-', 19) > 0 || iso.EndsWith('Z')))
                return DateTimeOffset.Parse(iso, CultureInfo.InvariantCulture).DateTime;
            return DateTime.Parse(iso, CultureInfo.InvariantCulture, DateTimeStyles.None);
        }
    }

    public string? TzName => _n is JsonObject o ? (string?)o["tz"] : null;

    // ------------------------------------------------------------------ access
    public J this[string key] => _n is JsonObject o && o.TryGetPropertyValue(key, out var v) ? new J(v) : None;
    public J this[int i]
    {
        get
        {
            if (_n is not JsonArray a) return None;
            if (i < 0) i += a.Count;
            return i >= 0 && i < a.Count ? new J(a[i]) : None;
        }
    }
    public J this[J key] => key.IsNumber && IsList ? this[(int)key.Num] : this[key.ToString()];

    /// <summary>dict.get(key, default)</summary>
    public J Get(object key, object? fallback = null)
    {
        var k = key is J jk ? jk.ToString() : Convert.ToString(key, CultureInfo.InvariantCulture)!;
        return _n is JsonObject o && o.TryGetPropertyValue(k, out var v) ? new J(v) : Of(fallback);
    }

    public bool Has(string key) => _n is JsonObject o && o.ContainsKey(key);

    /// <summary>Python `x or fallback`</summary>
    public J Or(object? fallback) => Truthy ? this : Of(fallback);

    public int Count => _n switch { JsonArray a => a.Count, JsonObject o => o.Count, JsonValue v when v.TryGetValue<string>(out var s) => s.EnumerateRunes().Count(), _ => 0 };

    /// <summary>Python slice list[start:end]</summary>
    public J Slice(int start, int? end = null)
    {
        var items = this.ToList();
        int n = items.Count;
        int s = start < 0 ? Math.Max(0, n + start) : Math.Min(start, n);
        int e = end is null ? n : (end < 0 ? Math.Max(0, n + end.Value) : Math.Min(end.Value, n));
        return Of(e > s ? items.GetRange(s, e - s) : new List<J>());
    }

    /// <summary>dict.items(): key/value pairs in order</summary>
    public IEnumerable<(J Key, J Value)> Items()
    {
        if (_n is JsonObject o)
            foreach (var kv in o) yield return (Of(kv.Key), new J(kv.Value));
    }

    public IEnumerator<J> GetEnumerator()
    {
        switch (_n)
        {
            case JsonArray a:
                foreach (var x in a) yield return new J(x);
                break;
            case JsonObject o:
                foreach (var kv in o) yield return Of(kv.Key);
                break;
        }
    }

    IEnumerator IEnumerable.GetEnumerator() => GetEnumerator();

    public override bool TryGetMember(GetMemberBinder binder, out object? result)
    {
        result = this[binder.Name];
        return true;
    }

    public override bool TryConvert(ConvertBinder binder, out object? result)
    {
        if (binder.Type == typeof(bool)) { result = Truthy; return true; }
        if (binder.Type == typeof(string)) { result = ToString(); return true; }
        if (binder.Type == typeof(int)) { result = (int)Num; return true; }
        if (binder.Type == typeof(long)) { result = Int; return true; }
        if (binder.Type == typeof(double)) { result = Num; return true; }
        if (binder.Type == typeof(IEnumerable<J>) || binder.Type == typeof(IEnumerable)) { result = this; return true; }
        result = null;
        return false;
    }

    // ------------------------------------------------------------------ comparisons (Python semantics for our data)
    private static bool Eq(J a, object? b)
    {
        if (b is J jb)
        {
            if (a.IsNone || jb.IsNone) return a.IsNone && jb.IsNone;
            if (a.IsNumber && jb.IsNumber) return a.Num == jb.Num;
            if (a.IsBool && jb.IsBool) return a.Truthy == jb.Truthy;
            return (a.IsString && jb.IsString) && a.Raw == jb.Raw;
        }
        return b switch
        {
            null => a.IsNone,
            string s => a.IsString && a.Raw == s,
            bool bo => a.IsBool && a.Truthy == bo,
            int i => a.IsNumber && a.Num == i,
            long l => a.IsNumber && a.Num == l,
            double d => a.IsNumber && a.Num == d,
            _ => false,
        };
    }

    public static bool operator ==(J a, object? b) => Eq(a, b);
    public static bool operator !=(J a, object? b) => !Eq(a, b);
    public static bool operator ==(J a, J b) => Eq(a, b);
    public static bool operator !=(J a, J b) => !Eq(a, b);
    public static bool operator ==(J a, string b) => Eq(a, b);
    public static bool operator !=(J a, string b) => !Eq(a, b);
    public static bool operator ==(J a, int b) => Eq(a, b);
    public static bool operator !=(J a, int b) => !Eq(a, b);
    public override bool Equals(object? obj) => Eq(this, obj);
    public override int GetHashCode() => ToString().GetHashCode();

    public static bool operator >(J a, J b) => a.Num > b.Num;
    public static bool operator <(J a, J b) => a.Num < b.Num;
    public static bool operator >=(J a, J b) => a.Num >= b.Num;
    public static bool operator <=(J a, J b) => a.Num <= b.Num;
    public static bool operator >(J a, double b) => a.Num > b;
    public static bool operator <(J a, double b) => a.Num < b;
    public static bool operator >=(J a, double b) => a.Num >= b;
    public static bool operator <=(J a, double b) => a.Num <= b;
    public static double operator +(J a, double b) => a.Num + b;
    public static double operator -(J a, double b) => a.Num - b;
    public static double operator *(J a, double b) => a.Num * b;

    // ------------------------------------------------------------------ Python str() / repr()
    public override string ToString()
    {
        switch (_n)
        {
            case null: return "None";
            case JsonObject o when o.ContainsKey("$str"): return (string)o["$str"]!;
            case JsonObject o when o.ContainsKey("s") && (o.ContainsKey("$dt") || o.ContainsKey("$d")): return (string)o["s"]!;
            case JsonArray or JsonObject: return Repr();
            case JsonValue v when v.TryGetValue<string>(out var s): return s;
            case JsonValue v when v.TryGetValue<bool>(out var b): return b ? "True" : "False";
            default: return PyNumber(_n.ToJsonString());
        }
    }

    /// <summary>Python repr(): used where the original templates printed lists or dicts.</summary>
    public string Repr()
    {
        switch (_n)
        {
            case JsonArray a:
                return "[" + string.Join(", ", a.Select(x => new J(x).Repr())) + "]";
            case JsonObject o when IsDate || o.ContainsKey("$str"):
                return ToString();
            case JsonObject o:
                return "{" + string.Join(", ", o.Select(kv => PyStrRepr(kv.Key) + ": " + new J(kv.Value).Repr())) + "}";
            case JsonValue v when v.TryGetValue<string>(out var s):
                return PyStrRepr(s);
            default:
                return ToString();
        }
    }

    private static string PyNumber(string json)
    {
        // json.dumps writes floats with repr(), so the raw JSON text already matches Python's str() for numbers
        return json;
    }

    public static string PyStrRepr(string s)
    {
        char q = s.Contains('\'') && !s.Contains('"') ? '"' : '\'';
        var sb = new StringBuilder();
        sb.Append(q);
        foreach (var r in s.EnumerateRunes())
        {
            int c = r.Value;
            if (c == '\\') sb.Append("\\\\");
            else if (c == q) sb.Append('\\').Append(q);
            else if (c == '\n') sb.Append("\\n");
            else if (c == '\r') sb.Append("\\r");
            else if (c == '\t') sb.Append("\\t");
            else if (c < 0x20 || c == 0x7f) sb.Append("\\x").Append(c.ToString("x2"));
            else sb.Append(r.ToString());
        }
        sb.Append(q);
        return sb.ToString();
    }
}
