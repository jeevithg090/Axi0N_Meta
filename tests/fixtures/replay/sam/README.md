# SAM 3.1 transport fixtures

Recorded-style replies in the transport envelope `{status, headers, json}` used by
`rulecam.clients.transport`. The output grammar follows dev.meta.ai/docs/sam/reading-segmentation;
mask payloads (`~FIXTURE-A`, …) are placeholders, decoded in tests by a fake `MaskDecoder`
because the real payload format is only implemented in Meta's `meta-sam-parser`.
Replace with real recordings once P3-02's replay cache records a live run.
