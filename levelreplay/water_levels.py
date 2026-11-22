from bisect import bisect_left


STATIONS = ('chamber','lower','upper')


def read_level(model, station, time):
    points=model['samples'][station]
    index=bisect_left(points,time,key=lambda p:p['time'])
    if index<len(points) and points[index]['time']==time:
        row=points[index]
        return row['level'], [row['id']]
    if index==0 or index==len(points):
        return None, []
    a,b=points[index-1:index+1]
    if b['time']-a['time']>model['max_gap']:
        return None,[a['id'],b['id']]
    return a['level']+(b['level']-a['level'])*(time-a['time'])/(b['time']-a['time']),[a['id'],b['id']]
