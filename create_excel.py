import pandas as pd

# Данные для матчей
data = {
    'игрок1': ['Андрей', 'Сергей', 'Юра', 'Андрей'],
    'игрок2': ['Петя', 'Искандер', 'Денисюка665', 'Сергей'],
    'победитель': ['Андрей', 'Искандер', 'Юра', 'Андрей']
}

df = pd.DataFrame(data)
df.to_excel('new-matches.xlsx', index=False)
print('✅ Файл matches.xlsx создан!')
print(f'📊 Добавлено матчей: {len(df)}')
print('\n📋 Содержимое файла:')
print(df)