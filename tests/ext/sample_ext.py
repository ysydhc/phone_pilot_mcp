def hello(*, a: int, ctx=None):
    if ctx is not None:
        ctx.vars["from_ext"] = a
    return {"ok": True, "value": a}
