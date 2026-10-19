import os, uuid, zipfile, urllib.request

with zipfile.ZipFile(os.path.join(os.environ["USERPROFILE"], "Downloads", "Flyer Beez Pay.zip")) as z:
    pdf = z.read([n for n in z.namelist() if n.lower().endswith(".pdf")][0])
b = uuid.uuid4().hex
body = (f'--{b}\r\nContent-Disposition: form-data; name="files"; filename="Flyer.pdf"\r\n'
        f'Content-Type: application/pdf\r\n\r\n').encode() + pdf + (
        f'\r\n--{b}\r\nContent-Disposition: form-data; name="level"\r\n\r\n7\r\n--{b}--\r\n').encode()
req = urllib.request.Request("http://127.0.0.1:8768/api/compress", data=body,
                             headers={"Content-Type": f"multipart/form-data; boundary={b}"})
with urllib.request.urlopen(req) as r:
    print(r.status, len(r.read()) // 1024, "KB", r.headers["content-disposition"])
