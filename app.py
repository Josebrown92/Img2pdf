import os
import uuid
import zipfile
from io import BytesIO
from flask import Flask, render_template, request, send_file, jsonify
from PIL import Image
from reportlab.lib.pagesizes import A4, letter, landscape
from reportlab.pdfgen import canvas

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024  # 50MB max

UPLOAD_FOLDER = os.path.join(os.path.dirname(__file__), 'uploads')
OUTPUT_FOLDER = os.path.join(os.path.dirname(__file__), 'outputs')
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'bmp', 'webp', 'tiff', 'tif'}

os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(OUTPUT_FOLDER, exist_ok=True)


def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


def image_to_pdf(image_path, page_size='A4', fit_mode='fit', orientation='portrait'):
    """Convert a single image to PDF bytes."""
    img = Image.open(image_path)

    # Convert to RGB if needed (handles RGBA, palette, etc.)
    if img.mode in ('RGBA', 'LA', 'P'):
        background = Image.new('RGB', img.size, (255, 255, 255))
        if img.mode == 'P':
            img = img.convert('RGBA')
        if img.mode in ('RGBA', 'LA'):
            background.paste(img, mask=img.split()[-1])
        else:
            background.paste(img)
        img = background
    elif img.mode != 'RGB':
        img = img.convert('RGB')

    # Page size
    sizes = {'A4': A4, 'Letter': letter}
    pw, ph = sizes.get(page_size, A4)

    if orientation == 'landscape':
        pw, ph = ph, pw

    buffer = BytesIO()
    c = canvas.Canvas(buffer, pagesize=(pw, ph))

    img_w, img_h = img.size
    margin = 20

    if fit_mode == 'fit':
        # Scale to fit within page with margins
        available_w = pw - 2 * margin
        available_h = ph - 2 * margin
        scale = min(available_w / img_w, available_h / img_h)
        draw_w = img_w * scale
        draw_h = img_h * scale
        x = (pw - draw_w) / 2
        y = (ph - draw_h) / 2
    elif fit_mode == 'fill':
        # Fill entire page
        draw_w, draw_h = pw, ph
        x, y = 0, 0
    else:  # original
        draw_w, draw_h = img_w, img_h
        x = (pw - draw_w) / 2
        y = (ph - draw_h) / 2

    # Save temp image for reportlab
    temp_path = image_path + '_temp.jpg'
    img.save(temp_path, 'JPEG', quality=95)
    c.drawImage(temp_path, x, y, width=draw_w, height=draw_h)
    c.save()
    os.remove(temp_path)

    buffer.seek(0)
    return buffer


def images_to_combined_pdf(image_paths, page_size='A4', fit_mode='fit', orientation='portrait'):
    """Combine multiple images into one multi-page PDF."""
    sizes = {'A4': A4, 'Letter': letter}
    pw, ph = sizes.get(page_size, A4)
    if orientation == 'landscape':
        pw, ph = ph, pw

    buffer = BytesIO()
    c = canvas.Canvas(buffer, pagesize=(pw, ph))
    margin = 20

    for i, image_path in enumerate(image_paths):
        if i > 0:
            c.showPage()

        img = Image.open(image_path)
        if img.mode in ('RGBA', 'LA', 'P'):
            background = Image.new('RGB', img.size, (255, 255, 255))
            if img.mode == 'P':
                img = img.convert('RGBA')
            if img.mode in ('RGBA', 'LA'):
                background.paste(img, mask=img.split()[-1])
            else:
                background.paste(img)
            img = background
        elif img.mode != 'RGB':
            img = img.convert('RGB')

        img_w, img_h = img.size

        if fit_mode == 'fit':
            available_w = pw - 2 * margin
            available_h = ph - 2 * margin
            scale = min(available_w / img_w, available_h / img_h)
            draw_w = img_w * scale
            draw_h = img_h * scale
            x = (pw - draw_w) / 2
            y = (ph - draw_h) / 2
        elif fit_mode == 'fill':
            draw_w, draw_h = pw, ph
            x, y = 0, 0
        else:
            draw_w, draw_h = img_w, img_h
            x = (pw - draw_w) / 2
            y = (ph - draw_h) / 2

        temp_path = image_path + '_temp.jpg'
        img.save(temp_path, 'JPEG', quality=95)
        c.drawImage(temp_path, x, y, width=draw_w, height=draw_h)
        os.remove(temp_path)

    c.save()
    buffer.seek(0)
    return buffer


@app.route('/')
def index():
    return render_template('index.html')


@app.route('/convert', methods=['POST'])
def convert():
    files = request.files.getlist('images')
    page_size = request.form.get('page_size', 'A4')
    fit_mode = request.form.get('fit_mode', 'fit')
    orientation = request.form.get('orientation', 'portrait')
    combine = request.form.get('combine', 'false') == 'true'

    if not files or all(f.filename == '' for f in files):
        return jsonify({'error': 'No files selected'}), 400

    valid_files = [f for f in files if f and allowed_file(f.filename)]
    if not valid_files:
        return jsonify({'error': 'No valid image files found'}), 400

    # Save uploaded files
    saved_paths = []
    for f in valid_files:
        ext = f.filename.rsplit('.', 1)[1].lower()
        filename = f'{uuid.uuid4().hex}.{ext}'
        path = os.path.join(UPLOAD_FOLDER, filename)
        f.save(path)
        saved_paths.append((path, f.filename))

    try:
        if combine or len(saved_paths) == 1:
            # Single combined PDF
            paths = [p for p, _ in saved_paths]
            if len(paths) == 1:
                pdf_buffer = image_to_pdf(paths[0], page_size, fit_mode, orientation)
                original_name = saved_paths[0][1].rsplit('.', 1)[0]
                output_name = f'{original_name}.pdf'
            else:
                pdf_buffer = images_to_combined_pdf(paths, page_size, fit_mode, orientation)
                output_name = 'combined_images.pdf'

            # Clean up uploads
            for path, _ in saved_paths:
                os.remove(path)

            return send_file(
                pdf_buffer,
                mimetype='application/pdf',
                as_attachment=True,
                download_name=output_name
            )
        else:
            # Multiple separate PDFs → zip
            zip_buffer = BytesIO()
            with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
                for path, original_name in saved_paths:
                    pdf_buffer = image_to_pdf(path, page_size, fit_mode, orientation)
                    pdf_name = original_name.rsplit('.', 1)[0] + '.pdf'
                    zf.writestr(pdf_name, pdf_buffer.read())
                    os.remove(path)

            zip_buffer.seek(0)
            return send_file(
                zip_buffer,
                mimetype='application/zip',
                as_attachment=True,
                download_name='converted_pdfs.zip'
            )

    except Exception as e:
        for path, _ in saved_paths:
            if os.path.exists(path):
                os.remove(path)
        return jsonify({'error': str(e)}), 500


if __name__ == '__main__':
    app.run(debug=False, host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
