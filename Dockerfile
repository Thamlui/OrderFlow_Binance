# Use an official lightweight Python image
FROM python:3.14-slim

# Set working directory
WORKDIR /app

# Prevent Python from writing pyc files and buffering stdout/stderr
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Install Python dependencies
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . /app

# Expose the port Cloud Run expects
EXPOSE 8080

# Run the Streamlit app on the container-provided port
CMD ["sh", "-c", "export STREAMLIT_SERVER_PORT=\"${PORT:-8080}\" && streamlit run dashboard.py --server.address=0.0.0.0 --server.port=$STREAMLIT_SERVER_PORT"]
