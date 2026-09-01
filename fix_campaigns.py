# fix_campaigns.py
import psycopg2
from dotenv import load_dotenv
import os

load_dotenv()

def fix_campaigns():
    try:
        conn = psycopg2.connect(os.getenv('DATABASE_URL', 'postgresql://postgres:janine123@localhost:5433/rtp_parrilla'))
        cur = conn.cursor()
        
        print("🔍 Verificando tabla campaigns...")
        
        # Verificar columnas actuales
        cur.execute("""
            SELECT column_name 
            FROM information_schema.columns 
            WHERE table_name = 'campaigns'
        """)
        columns = [row[0] for row in cur.fetchall()]
        print(f"Columnas actuales: {columns}")
        
        # Agregar color
        if 'color' not in columns:
            print("⚠️ Agregando columna color...")
            cur.execute("ALTER TABLE campaigns ADD COLUMN color VARCHAR(7) DEFAULT '#28a745'")
            print("✅ Columna color agregada")
        else:
            print("✅ Columna color ya existe")
        
        # Agregar is_active
        if 'is_active' not in columns:
            print("⚠️ Agregando columna is_active...")
            cur.execute("ALTER TABLE campaigns ADD COLUMN is_active BOOLEAN DEFAULT TRUE")
            print("✅ Columna is_active agregada")
        else:
            print("✅ Columna is_active ya existe")
        
        conn.commit()
        
        # Verificar nuevamente
        cur.execute("""
            SELECT column_name 
            FROM information_schema.columns 
            WHERE table_name = 'campaigns'
        """)
        columns = [row[0] for row in cur.fetchall()]
        print(f"\nColumnas después: {columns}")
        
        cur.close()
        conn.close()
        
        print("\n✅ Todas las columnas de campaigns están listas")
        print("Ahora ejecuta: flask run")
        
    except Exception as e:
        print(f"❌ Error: {e}")

if __name__ == '__main__':
    fix_campaigns()