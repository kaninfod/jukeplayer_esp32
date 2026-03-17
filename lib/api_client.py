import requests
import gc
import time

class MediaPlayerAPI:
    """Client for the media player REST API."""
    
    def __init__(self, host, port=8000):
        """Initialize API client.
        
        Args:
            host: API server hostname/IP
            port: API server port
        """
        self.base_url = f"http://{host}:{port}/api/mediaplayer"
    
    def _parse_track_info(self, data):
        """Parse and normalize track info from API responses.
        
        Handles multiple response formats and returns standardized dict.
        
        Args:
            data: Raw API response dict
            
        Returns:
            dict: Normalized track info with keys: title, album, artist, status, volume
        """
        # Handle nested "payload" structure (WebSocket/REST poll)
        if "payload" in data:
            payload = data["payload"]
            track_data = payload.get("current_track", {})
            return {
                "title": track_data.get("title", ""),
                "album": track_data.get("album", ""),
                "artist": track_data.get("artist", ""),
                "status": payload.get("status", ""),
                "volume": payload.get("volume", 0)
            }
        
        # Handle "current_track_info" structure (play_album response)
        if "current_track_info" in data:
            ctl_info = data["current_track_info"]
            track_data = ctl_info.get("current_track", {})
            return {
                "title": track_data.get("title", ""),
                "album": track_data.get("album", ""),
                "artist": track_data.get("artist", ""),
                "status": ctl_info.get("status", ""),
                "volume": ctl_info.get("volume", 0)
            }
        
        # Fallback for unexpected format
        return None
    
    def get_status(self, timeout=5):
        """Get current playback status.
        
        Returns:
            dict: Track info with keys: title, album, artist, status, volume or None if failed
        """
        print("LOG: Polling status...")
        gc.collect()
        
        try:
            res = requests.get(f"{self.base_url}/status", timeout=timeout)
            print(f"LOG: Response status code: {res.status_code}")
            
            if res.status_code != 200:
                print(f"LOG: Non-200 status code: {res.status_code}")
                res.close()
                return None
            
            data = res.json()
            res.close()
            
            # Log the full response to see structure
            print(f"LOG: API response: {data}")
            
            track_info = self._parse_track_info(data)
            if track_info:
                print(f"LOG: Extracted status='{track_info.get('status')}' volume={track_info.get('volume')}")
            
            return track_info
            
        except Exception as e:
            print(f"LOG: Poll Error: {e}")
            return None
    
    def play_album(self, album_id, timeout=8):
        """Request playback of an album.
        
        Args:
            album_id: Album identifier (e.g., 'al-138')
            timeout: Request timeout in seconds
            
        Returns:
            dict: Track info on success (title, album, artist, status, volume)
            False: If request failed
        """
        if not album_id or len(album_id) < 1:
            print("LOG: Album ID is empty or unreadable.")
            return False
        
        print(f"LOG: Requesting Album ID: {album_id}")
        gc.collect()
        
        url = f"{self.base_url}/play_album_from_albumid/{album_id}?start_track_index=0"
        print(f"LOG: Free memory: {gc.mem_free()} bytes")
        
        try:
            res = requests.post(url, timeout=timeout)
            print(f"LOG: API {res.status_code}: play_album response")
            
            if res.status_code != 200:
                res.close()
                return False
            
            data = res.json()
            res.close()
            
            track_info = self._parse_track_info(data)
            if track_info:
                print(f"LOG: play_album returned: {track_info.get('title')} - status={track_info.get('status')} vol={track_info.get('volume')}")
            
            return track_info if track_info else False
            
        except Exception as e:
            print(f"LOG: API POST Failed: {e}")
            return False
    
    def play_pause(self):
        """Toggle play/pause."""
        return self._post_command("/play_pause")
    
    def next_track(self):
        """Skip to next track."""
        return self._post_command("/next_track")
    
    def previous_track(self):
        """Skip to previous track."""
        return self._post_command("/previous_track")
    
    def stop(self):
        """Stop playback."""
        return self._post_command("/stop")
    
    def _post_command(self, endpoint, timeout=5):
        """Send a POST command to the API.
        
        Args:
            endpoint: API endpoint path
            timeout: Request timeout in seconds
            
        Returns:
            bool: True if successful
        """
        gc.collect()
        
        try:
            url = f"{self.base_url}{endpoint}"
            res = requests.post(url, timeout=timeout)
            success = res.status_code == 200
            print(f"LOG: API {endpoint} {res.status_code}")
            res.close()
            return success
        except Exception as e:
            print(f"LOG: API {endpoint} failed: {e}")
            return False
