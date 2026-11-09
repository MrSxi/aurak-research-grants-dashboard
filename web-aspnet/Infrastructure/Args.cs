namespace AurakWeb.Infrastructure;

/// <summary>Arguments for a shared partial view (badge, grant card, match card...).</summary>
public sealed record Args(dynamic? A = null, dynamic? B = null, dynamic? C = null);
