# Flask ModelV 3D Grid Controller

This repository contains a Flask web application for interactive 3D grid visualization and control. The frontend uses Three.js to render a 6x10 grid of cubes, allowing users to hover and click on cube faces, with real-time feedback sent to the backend.

## Features

- Interactive 3D grid (6 columns × 10 rows) rendered in the browser
- Mouse hover highlights cube faces (front and right)
- Click to send cube coordinates to the Flask backend
- Responsive camera controls and smooth user experience
- Real-time status feedback from the server

## Technologies

- Python (Flask)
- JavaScript (Three.js, OrbitControls)
- HTML/CSS

## Getting Started

1. **Install dependencies**
   ```pip install flask```

2. **Run the server**
    ```python app.py```

3. **Open in browser Visit http://localhost:8000 to use the 3D grid controller.**

## File Structure

```
flask_modelV/
├── app.py                # Flask backend
├── static/
│   └── app.js            # Three.js frontend logic
├── templates/
│   └── index.html        # Main HTML page
└── README.md             # Project documentation
```

## Usage

- Hover over cube faces to highlight them.
- Click a face to send its coordinates to the server.
- The status bar displays server responses.

## License

MIT License
