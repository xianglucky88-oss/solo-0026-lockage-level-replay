def volume(sections, level):
    if level is None or level<sections[0]['level']:
        return None
    total=0
    for index,item in enumerate(sections):
        ceiling=sections[index+1]['level'] if index+1<len(sections) else level
        total+=max(0,min(ceiling,level)-item['level'])*item['area']
    return total
