# test_gemini.py
import requests

API_KEY = "AIzaSyBBANh5pphEpwPGUy3pEN3HHIcnJWV9-gs"

def test_gemini():
    print("🔍 Probando API Key de Gemini...")
    
    # Probar diferentes modelos
    models = ['gemini-1.5-flash', 'gemini-pro']
    
    for model in models:
        url = f"https://generativelanguage.googleapis.com/v1/models/{model}:generateContent?key={API_KEY}"
        
        payload = {
            "contents": [{
                "parts": [{"text": "Responde con 'OK' si estás funcionando"}]
            }]
        }
        
        try:
            response = requests.post(url, json=payload, timeout=10)
            print(f"\n📡 Modelo: {model}")
            print(f"   Status: {response.status_code}")
            
            if response.status_code == 200:
                data = response.json()
                result = data.get('candidates', [{}])[0].get('content', {}).get('parts', [{}])[0].get('text', '')
                print(f"   ✅ Respuesta: {result}")
                return True
            else:
                print(f"   ❌ Error: {response.text[:200]}")
                
        except Exception as e:
            print(f"   ❌ Excepción: {e}")
    
    return False

if __name__ == '__main__':
    test_gemini()