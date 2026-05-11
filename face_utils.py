import cv2
import face_recognition
import numpy as np
import io

def encode_face_from_image(image_bytes):
    """
    Takes an image (in bytes), decodes it using OpenCV, and extracts the primary face encoding.
    Returns the encoding as numpy array or None if no face found.
    """
    try:
        # Convert bytes to numpy array
        nparr = np.frombuffer(image_bytes, np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        
        # Convert image from BGR (OpenCV) to RGB (face_recognition)
        rgb_img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
        
        # Find all faces in the image
        face_locations = face_recognition.face_locations(rgb_img)
        if not face_locations:
            return None
            
        # Extract face encodings
        face_encodings = face_recognition.face_encodings(rgb_img, face_locations)
        if not face_encodings:
            return None
            
        return face_encodings[0]
    except Exception as e:
        print(f"Error encoding face: {e}")
        return None

def serialize_encoding(encoding):
    """ Converts a numpy array encoding to bytes for SQLite BLOB storage """
    return encoding.tobytes()

def deserialize_encoding(encoding_bytes):
    """ Converts bytes from SQLite BLOB back to a numpy array """
    if not encoding_bytes or len(encoding_bytes) != 1024:
        # 128 floats * 8 bytes each = 1024 bytes
        return None
    try:
        return np.frombuffer(encoding_bytes, dtype=np.float64)
    except Exception as e:
        print(f"Error deserializing encoding: {e}")
        return None

def match_face(known_encodings, known_ids, face_encoding_to_check, tolerance=0.55):
    """
    Compares the given face encoding with a list of known encodings.
    Returns the matched employee_id or None.
    """
    if not known_encodings:
        return None
        
    matches = face_recognition.compare_faces(known_encodings, face_encoding_to_check, tolerance=tolerance)
    face_distances = face_recognition.face_distance(known_encodings, face_encoding_to_check)
    
    # Get the best match
    best_match_index = np.argmin(face_distances)
    if matches[best_match_index]:
        return known_ids[best_match_index]
        
    return None
