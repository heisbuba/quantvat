from google import genai
from google.genai import types
import traceback
from ..config import db, get_user_keys

# Cap stored/replayed conversation turns so Firestore docs 
MAX_HISTORY_MESSAGES = 14

class AuditorEngine:
    @staticmethod
    def _get_client(api_key):
        return genai.Client(api_key=api_key)

    @staticmethod
    def initialize_firebase_session(uid, context):
        try:
            keys = get_user_keys(uid)
            api_key = keys.get('gemini_key')
            if not api_key: 
                return "Error: No API Key found in Settings."

            client = AuditorEngine._get_client(api_key)
            
            # Parsona
            instruction = f"""
            PERSONA:
            You are the QuantVAT AI Trading Journal Auditor, a senior Risk Manager and Trading Psychologist with 50 years trading experience like a Market Wizard. 
            Speak with veteran authority. Tone is blunt but constructive.

            MANDATE:
            1. Analyze the 'WHY' behind execution based on the provided logs.
            2. STRUCTURE: 
               - ## 📊 OVERVIEW: 2-sentence performance reality check.
               - ## 🚩 RED FLAGS: Top 2 execution errors (FOMO, sizing, fear, etc).
               - ## 💡 THE REMEDY: One specific, actionable rule for the next session.
            3. FORMAT: Use bold text for emphasis. 
            4. NO TABLES: Use bullet points only.
            5. INTERACTION: End with a provocative question about a specific trade.

            TRADING LEDGER (CSV FORMAT):
            {context}
            """

            prompt = "Analyze my execution performance based on the CSV data above. End with: 'I have analyzed your data. Ready for audit.'"
            
            response = client.models.generate_content(
                model='gemini-3.5-flash', 
                contents=prompt,
                config=types.GenerateContentConfig(
                    system_instruction=instruction
                )
            )
            
            # Preserve prior conversation turns instead of wiping them
            user_doc = db.collection('users').document(uid).get()
            prior_history = (user_doc.to_dict() or {}).get("ai_history", []) if user_doc.exists else []
            history = (prior_history + [
                {"role": "user", "parts": [{"text": prompt}]},
                {"role": "model", "parts": [{"text": response.text}]}
            ])[-MAX_HISTORY_MESSAGES:]
            
            db.collection('users').document(uid).set({
                "ai_history": history,
                "ai_context": context
            }, merge=True)
            
            return response.text
        except Exception as e:
            print(f"AI Init Error: {traceback.format_exc()}")
            return f"System Error: {str(e)}"

    @staticmethod
    def continue_firebase_chat(uid, prompt):
        try:
            user_doc = db.collection('users').document(uid).get()
            data = user_doc.to_dict() if user_doc.exists else {}
            history = data.get("ai_history", [])
            context = data.get("ai_context", "") 
            
            api_key = get_user_keys(uid).get('gemini_key')
            if not api_key: 
                return "Error: API Key missing."

            client = AuditorEngine._get_client(api_key)

            # Keep only the most recent turns when replaying to the model
            if len(history) > MAX_HISTORY_MESSAGES:
                history = history[-MAX_HISTORY_MESSAGES:]

            # Robust mapping
            contents = []
            for h in history:
                p = h['parts'][0]
                text_content = p['text'] if isinstance(p, dict) else str(p)
                contents.append(types.Content(
                    role=h['role'], 
                    parts=[types.Part.from_text(text=text_content)]
                ))
            
            contents.append(types.Content(role="user", parts=[types.Part.from_text(text=prompt)]))

            # Re-injects Persona
            instruction = f"PERSONA: QuantVAT AI Trading Journal Auditor. Senior Risk Manager.\nDATA:\n{context}"

            response = client.models.generate_content(
                model='gemini-3.5-flash',
                contents=contents,
                config=types.GenerateContentConfig(
                    system_instruction=instruction
                )
            )
            
            # Append new turn and sync to Firestore
            history.append({"role": "user", "parts": [{"text": prompt}]})
            history.append({"role": "model", "parts": [{"text": response.text}]})
            if len(history) > MAX_HISTORY_MESSAGES:
                history = history[-MAX_HISTORY_MESSAGES:]
            db.collection('users').document(uid).set({"ai_history": history}, merge=True)
            
            return response.text
        except Exception as e:
            print(f"AI Chat Error: {traceback.format_exc()}")
            return f"Auditor Error: {str(e)}"