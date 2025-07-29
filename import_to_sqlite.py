import pandas as pd
import sqlite3

df = pd.read_csv('15m_data.csv', sep='\t')

conn = sqlite3.connect('market_data.db')
df.to_sql('market_data', conn, if_exists='replace', index=False)
conn.close()