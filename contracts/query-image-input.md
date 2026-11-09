# Query image input

R19 reference-image retrieval uses an immutable query artifact; it never uses a
VLM caption as a substitute for an image vector.

`query-spec@2.query_input` is either absent or exactly:

```json
{
  "type": "reference_image",
  "query_image_id": "qimg_<uuid>",
  "digest": "sha256:<64 hex>",
  "exclude_exact_content": false
}
```

Draft creation re-authorizes the image against the draft owner and project and
rechecks the digest. The confirmed proposal freezes all four fields. Route
discovery exposes only bindings that the installed image encoder can encode
with the exact `encoder_ref`, preprocessing identity, vector dimension and
metric. Plan preparation rechecks compatibility and embeds the reference bytes;
the user's text remains catalog-selection and post-retrieval cleaning context.

`exclude_exact_content` is opt-in. When true, candidate manifest construction
removes mappings whose authoritative SHA-256 equals the frozen reference digest.
It is not near-duplicate detection and does not remove visually similar content.
