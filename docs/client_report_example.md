# Camera Connectivity Report — New Facility Onboarding

**Date:** August 15, 2026
**Prepared by:** Dnyaneshwari, Protex AI Technical Support & Solutions

## Summary

We tested the 2 camera streams provided for your site. Both connected
successfully and are ready to be added to the Protex platform. No action is
needed from you at this time — see the note below for one small detail
worth being aware of.

## Results

| Camera Name | Status | Notes |
|---|---|---|
| Exterior Street View | ✅ Connected | Screenshot confirmed, image is clear. Attached: `Exterior_Street_View.jpg` |
| Outside Junction | ✅ Connected | Screenshot confirmed, image is clear. Attached: `Outside_Junction.jpg` |

## A note on the URLs provided

Both camera URLs you sent us are working correctly and produced clear
images. One technical detail for your awareness: both are using an
HTTP-based video format rather than RTSP (the format we typically expect).
This doesn't affect our ability to connect today, but if your cameras also
support a true RTSP stream, that's usually a slightly more efficient option
for us to use going forward. No action needed right now — just flagging it
in case it's useful for future reference or if you're in touch with your
camera vendor.

## What happens next

Both cameras are ready to be added to the Protex platform. We'll proceed
with onboarding using the URLs provided. If anything changes on your end
(new IP addresses, camera replacements, etc.) just let us know and we'll
re-test.

Please let us know if you have any questions.

---
*This report was generated with the assistance of Protex's internal camera
connectivity validation tool. Screenshots referenced above are included in
this submission's `examples/screenshots/` folder.*

## Appendix — example of a failed-camera entry (for illustration only)

The section below shows how this report would look if a camera did **not**
connect. This is illustrative only — it is not part of the real results
above, and uses the tool's error-handling demo data rather than this
client's actual cameras, purely to show what a client would see in that
scenario:

| Camera Name | Issue | What we'd need from you |
|---|---|---|
| Example: Unreachable Host | We couldn't reach this camera's IP address on your network. | Please confirm the IP address is correct and that no firewall rule is blocking access from our device. |
| Example: Wrong Path | The camera responded, but the video path in the URL doesn't exist on the camera. | Could you confirm the correct streaming URL/path, or share the camera's make/model so we can look it up? |
