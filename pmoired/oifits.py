import numpy as np
from astropy.io import fits
import scipy.signal
import copy

def loadOI(filename, insname=None, targname=None, verbose=True,
           withHeader=False, medFilt=None, tellurics=None, debug=False,
           binning=None):
    """
    load OIFITS "filename" and return a dict:

    'WL': wavelength (1D array)
    'OI_VIS2': dict index by baseline. each baseline is a dict as well:
        ['V2', 'EV2', 'u', 'v', 'MJD'] v2 and ev2 1d array like wl, u and v are scalar
    'OI_VIS': dict index by baseline. each baseline is a dict as well:
        ['|V|', 'E|V|', 'PHI', 'EPHI', 'u', 'v', 'MJD'] (phases in degrees)
    'OI_T3': dict index by baseline. each baseline is a dict as well:
        [AMP', 'EAMP', 'PHI', 'EPHI', 'u1', 'v1', 'u2', 'v2', 'MJD'] (phases in degrees)

    can contains other things, *but None can start with 'OI_'*:
    'filename': name of the file
    'insname': name of the instrument (default: all of them)
    'targname': name of the instrument (default: single one if only one)
    'header': full header of the file if "withHeader" is True (default False)

    All variables are keyed by the name of the baseline (or triplet). e.g.:

    oi['OI_VIS2']['V2'][baseline] is a 2D array: shape (len(u), len(wl))

    One can specify "insname" for instrument name. If None provided, returns as
    many dictionnary as they are instruments (either single dict for single
    instrument, of list of dictionnary).

    """
    if debug:
        print('DEBUG: loadOI', filename)
    if type(filename)!=str:
        res = []
        for f in filename:
            tmp = loadOI(f, insname=insname, withHeader=withHeader,
                         medFilt=medFilt, tellurics=tellurics, targname=targname,
                         verbose=verbose, debug=debug, binning=binning)
            if type(tmp)==list:
                res.extend(tmp)
            elif type(tmp)==dict:
                res.append(tmp)
        return res

    res = {}
    h = fits.open(filename)

    # -- how many instruments?
    instruments = []
    for hdu in h:
        if 'EXTNAME' in hdu.header and hdu.header['EXTNAME']=='OI_WAVELENGTH':
            instruments.append(hdu.header['INSNAME'])
        if 'EXTNAME' in hdu.header and hdu.header['EXTNAME']=='OI_TARGET':
            targets = {hdu.data['TARGET'][i].strip():hdu.data['TARGET_ID'][i] for
                        i in range(len(hdu.data['TARGET']))}
            # -- weird case when targets is defined multiple times
            targets = {}
            for i in range(len(hdu.data['TARGET'])):
                k = hdu.data['TARGET'][i].strip()
                if not k in targets:
                    targets[k] = hdu.data['TARGET_ID'][i]
                else:
                    if type(targets[k])!=list:
                        targets[k] = [targets[k], hdu.data['TARGET_ID'][i]]
                    else:
                        targets[k].append(hdu.data['TARGET_ID'][i])
    if targname is None and len(targets)==1:
        targname = list(targets.keys())[0]
    assert targname in targets.keys(), 'unknown target "'+str(targname)+'", '+\
        'should be in ['+', '.join(['"'+t+'"' for t in list(targets.keys())])+']'

    if insname is None:
        if len(instruments)==1:
            insname = instruments[0]
        else:
            h.close()
            print('insname not specified, loading %s'%str(instruments))
            # -- return list: one dict for each insname
            return [loadOI(filename, insname=ins, withHeader=withHeader,
                            medFilt=medFilt) for ins in instruments]

    assert insname in instruments, 'unknown instrument "'+insname+'", '+\
        'should be in ['+', '.join(['"'+t+'"' for t in instruments])+']'

    res['insname'] = insname
    # -- spectral resolution
    res['filename'] = filename
    res['targname'] = targname

    # -- for now, only catching ESO pipelines, in the future we can add more
    if 'ESO PRO REC1 PIPE ID' in h[0].header:
        res['pipeline'] = h[0].header['ESO PRO REC1 PIPE ID']
    else:
        res['pipeline'] = ''

    if withHeader:
        res['header'] = h[0].header

    if verbose:
        print('loadOI: loading', res['filename'])
        print('  > insname:', '"'+insname+'"','targname:', '"'+targname+'"',
                'pipeline:', '"'+res['pipeline']+'"')
    try:
        # -- VLTI 4T specific
        OPL = {}
        for i in range(4):
            T = h[0].header['ESO ISS CONF STATION%d'%(i+1)]
            opl = 0.5*(h[0].header['ESO DEL DLT%d OPL START'%(i+1)] +
                       h[0].header['ESO DEL DLT%d OPL END'%(i+1)])
            opl += h[0].header['ESO ISS CONF A%dL'%(i+1)]
            OPL[T] = opl
        res['OPL'] = OPL
        T = np.mean([h[0].header['ESO ISS TEMP TUN%d'%i] for i in [1,2,3,4]]) # T in C
        P = h[0].header['ESO ISS AMBI PRES'] # pressure in mbar
        H = h[0].header['ESO ISS AMBI RHUM'] # relative humidity: TODO outside == inside probably no ;(
        #print('T(C), P(mbar), H(%)', T, P, H)
        res['n_lab'] = n_JHK(res['WL'].astype(np.float64), 273.15+T, P, H)
    except:
        pass

    for hdu in h:
        if 'EXTNAME' in hdu.header and hdu.header['EXTNAME']=='OI_WAVELENGTH' and\
            hdu.header['INSNAME']==insname:
            # -- OIFITS in m, here we want um
            res['WL'] = np.array(hdu.data['EFF_WAVE'], dtype=np.float64)*1e6
            res['dWL'] = np.array(hdu.data['EFF_BAND'], dtype=np.float64)*1e6
            if not binning is None:
                # -- keep track of true wavelength
                _WL = res['WL']*1.0
                _dWL = res['dWL']*1.0
                # -- binned
                res['WL'] = np.linspace(res['WL'].min(),
                                        res['WL'].max(),
                                        len(res['WL'])//binning)
                res['dWL'] = binning*np.interp(res['WL'], _WL, _dWL)

            if debug:
                print('DEBUG: OI_WAVELENGTH')
                print(' | WL', res['WL'])
                print(' | dWL', res['dWL'])
    assert 'WL' in res, 'OIFITS is inconsistent: no wavelength table for insname="%s"'%(insname)

    oiarrays = {}
    for ih, hdu in enumerate(h):
        if 'EXTNAME' in hdu.header and hdu.header['EXTNAME']=='OI_ARRAY':
            oiarrays[hdu.header['ARRNAME']] = dict(zip(h['OI_ARRAY'].data['STA_INDEX'],
                           np.char.strip(h['OI_ARRAY'].data['STA_NAME'])))

    # -- assumes there is only one array!
    #oiarray = dict(zip(h['OI_ARRAY'].data['STA_INDEX'],
    #               np.char.strip(h['OI_ARRAY'].data['STA_NAME'])))


    # -- V2 baselines == telescopes pairs
    res['OI_VIS2'] = {}
    res['OI_VIS'] = {}
    res['OI_T3'] = {}
    res['OI_FLUX'] = {}
    for ih, hdu in enumerate(h):
        if 'EXTNAME' in hdu.header and hdu.header['EXTNAME']=='TELLURICS' and\
                    len(hdu.data['TELL_TRANS'])==len(res['WL']):
            res['TELLURICS'] = hdu.data['TELL_TRANS']
        if 'EXTNAME' in hdu.header and hdu.header['EXTNAME']=='OI_FLUX' and\
                    hdu.header['INSNAME']==insname:
            w = wTarg(hdu, targname, targets)
            if not any(w):
                print('  > \033[33mWARNING\033[0m: no data in OI_FLUX [HDU #%d]  for target="%s"/target_id='%(
                      ih, targname), targets[targname])
                continue
            oiarray = oiarrays[hdu.header['ARRNAME']]
            sta1 = [oiarray[s] for s in hdu.data['STA_INDEX']]
            for k in set(sta1):
                w = (np.array(sta1)==k)*wTarg(hdu, targname, targets)
                try:
                    # GRAVITY Data have non-standard naming :(
                    res['OI_FLUX'][k] = {'FLUX':hdu.data['FLUX'][w].reshape(w.sum(), -1),
                                        'EFLUX':hdu.data['FLUXERR'][w].reshape(w.sum(), -1),
                                        'FLAG':hdu.data['FLAG'][w].reshape(w.sum(), -1),
                                        'MJD':hdu.data['MJD'][w],
                                         }

                except:
                    res['OI_FLUX'][k] = {'FLUX':hdu.data['FLUXDATA'][w].reshape(w.sum(), -1),
                                        'EFLUX':hdu.data['FLUXERR'][w].reshape(w.sum(), -1),
                                        'FLAG':hdu.data['FLAG'][w].reshape(w.sum(), -1),
                                        'MJD':hdu.data['MJD'][w],
                                         }

                if any(w):
                    res['OI_FLUX'][k]['FLAG'] = np.logical_or(res['OI_FLUX'][k]['FLAG'],
                                                              ~np.isfinite(res['OI_FLUX'][k]['FLUX']))
                    res['OI_FLUX'][k]['FLAG'] = np.logical_or(res['OI_FLUX'][k]['FLAG'],
                                                              ~np.isfinite(res['OI_FLUX'][k]['EFLUX']))
                    if not binning is None:
                        # -- Note the binning of flux is not weighted, because
                        # -- it would make the tellurics correction incorrect
                        # -- overwise
                        res['OI_FLUX'][k]['FLUX'], flag = \
                                                    binOI(res['WL'], _WL,
                                                           res['OI_FLUX'][k]['FLUX'],
                                                           res['OI_FLUX'][k]['FLAG'],
                                                           medFilt=medFilt, retFlag=True)
                        # -- KLUDGE!
                        res['OI_FLUX'][k]['EFLUX'] = binOI(res['WL'], _WL,
                                                           res['OI_FLUX'][k]['EFLUX'],
                                                           res['OI_FLUX'][k]['FLAG'],
                                                           medFilt=medFilt)
                        res['OI_FLUX'][k]['FLAG'] = flag


        elif 'EXTNAME' in hdu.header and hdu.header['EXTNAME']=='OI_VIS2' and\
                    hdu.header['INSNAME']==insname:
            #w = hdu.data['TARGET_ID']==targets[targname]
            w = wTarg(hdu, targname, targets)

            if not any(w):
                print('  > \033[33mWARNING\033[0m: no data in OI_VIS2 [HDU #%d]  for target="%s"/target_id='%(
                            ih, targname), targets[targname])
                continue
            oiarray = oiarrays[hdu.header['ARRNAME']]
            sta2 = [oiarray[s[0]]+oiarray[s[1]] for s in hdu.data['STA_INDEX']]
            if debug:
                print('DEBUG: loading OI_VIS2', set(sta2))
            for k in set(sta2):
                w = (np.array(sta2)==k)*wTarg(hdu, targname, targets)
                #if debug:
                #    print(' | ', k, w)
                if k in res['OI_VIS2'] and any(w):
                    for k1, k2 in [('V2', 'VIS2DATA'), ('EV2', 'VIS2ERR'), ('FLAG', 'FLAG')]:
                        res['OI_VIS2'][k][k1] = np.append(res['OI_VIS2'][k][k1],
                                                          hdu.data[k2][w].reshape(w.sum(), -1), axis=0)
                    for k1, k2 in [('u', 'UCOORD'), ('v', 'VCOORD'), ('MJD','MJD')]:
                        res['OI_VIS2'][k][k1] = np.append(res['OI_VIS2'][k][k1],
                                                          hdu.data[k2][w])
                    tmp = hdu.data['UCOORD'][w][:,None]/res['WL'][None,:]
                    res['OI_VIS2'][k]['u/wl'] = np.append(res['OI_VIS2'][k]['u/wl'], tmp, axis=0)

                    tmp = hdu.data['VCOORD'][w][:,None]/res['WL'][None,:]
                    res['OI_VIS2'][k]['v/wl'] = np.append(res['OI_VIS2'][k]['v/wl'], tmp, axis=0)

                    res['OI_VIS2'][k]['FLAG'] = np.logical_or(res['OI_VIS2'][k]['FLAG'],
                                                              ~np.isfinite(res['OI_VIS2'][k]['V2']))
                    res['OI_VIS2'][k]['FLAG'] = np.logical_or(res['OI_VIS2'][k]['FLAG'],
                                                              ~np.isfinite(res['OI_VIS2'][k]['EV2']))
                elif any(w):
                    res['OI_VIS2'][k] = {'V2':hdu.data['VIS2DATA'][w].reshape(w.sum(), -1),
                                         'EV2':hdu.data['VIS2ERR'][w].reshape(w.sum(), -1),
                                         'u':hdu.data['UCOORD'][w],
                                         'v':hdu.data['VCOORD'][w],
                                         'MJD':hdu.data['MJD'][w],
                                         'u/wl': hdu.data['UCOORD'][w][:,None]/
                                                res['WL'][None,:],
                                         'v/wl': hdu.data['VCOORD'][w][:,None]/
                                                res['WL'][None,:],
                                         'FLAG':hdu.data['FLAG'][w].reshape(w.sum(), -1)
                                        }
                    res['OI_VIS2'][k]['FLAG'] = np.logical_or(res['OI_VIS2'][k]['FLAG'],
                                                              ~np.isfinite(res['OI_VIS2'][k]['V2']))
                    res['OI_VIS2'][k]['FLAG'] = np.logical_or(res['OI_VIS2'][k]['FLAG'],
                                                              ~np.isfinite(res['OI_VIS2'][k]['EV2']))
                if any(w):
                    res['OI_VIS2'][k]['B/wl'] = np.sqrt(res['OI_VIS2'][k]['u/wl']**2+
                                                        res['OI_VIS2'][k]['v/wl']**2)
                    res['OI_VIS2'][k]['PA'] = np.angle(res['OI_VIS2'][k]['v/wl']+
                                                    1j*res['OI_VIS2'][k]['u/wl'], deg=True)
                    if not binning is None:
                        res['OI_VIS2'][k]['V2'], flag =\
                                                   binOI(res['WL'], _WL,
                                                         res['OI_VIS2'][k]['V2'],
                                                         res['OI_VIS2'][k]['FLAG'],
                                                         res['OI_VIS2'][k]['EV2'],
                                                         medFilt=medFilt,
                                                         retFlag=True)

                        # -- KLUDGE!
                        res['OI_VIS2'][k]['EV2'] = binOI(res['WL'], _WL,
                                                         res['OI_VIS2'][k]['EV2'],
                                                         res['OI_VIS2'][k]['FLAG'],
                                                         res['OI_VIS2'][k]['EV2'],
                                                         medFilt=medFilt)
                        res['OI_VIS2'][k]['FLAG'] = flag


        # -- V baselines == telescopes pairs
        elif 'EXTNAME' in hdu.header and hdu.header['EXTNAME']=='OI_VIS' and\
                    hdu.header['INSNAME']==insname:
            #w = hdu.data['TARGET_ID']==targets[targname]
            w = wTarg(hdu, targname, targets)
            if not any(w):
                print('  > \033[33mWARNING\033[0m: no data in OI_VIS [HDU #%d]  for target="%s"/target_id='%(
                            ih, targname), targets[targname])
                continue
            oiarray = oiarrays[hdu.header['ARRNAME']]
            sta2 = [oiarray[s[0]]+oiarray[s[1]] for s in hdu.data['STA_INDEX']]
            if debug:
                print('DEBUG: loading OI_VIS', set(sta2))
                #print(' | ', targets[targname], hdu.data['TARGET_ID'])

            for k in set(sta2):
                w = (np.array(sta2)==k)*wTarg(hdu, targname, targets)
                #if debug:
                #    print(' | ', k, any(w))
                if k in res['OI_VIS'] and any(w):
                    for k1, k2 in [('|V|', 'VIS2AMP'), ('E|V|', 'VISAMPERR'),
                                    ('PHI', 'VISPHI'), ('EPHI', 'VISPHIERR'),
                                    ('FLAG', 'FLAG')]:
                        res['OI_VIS'][k][k1] = np.append(res['OI_VIS'][k][k1],
                                                         hdu.data[k2][w].reshape(w.sum(), -1), axis=0)
                    for k1, k2 in [('u', 'UCOORD'), ('v', 'VCOORD'), ('MJD', 'MJD')]:
                        res['OI_VIS'][k][k1] = np.append(res['OI_VIS'][k][k1],
                                                          hdu.data[k2][w])
                    tmp = hdu.data['UCOORD'][w][:,None]/res['WL'][None,:]
                    res['OI_VIS'][k]['u/wl'] = np.append(res['OI_VIS'][k]['u/wl'], tmp, axis=0)
                    tmp = hdu.data['VCOORD'][w][:,None]/res['WL'][None,:]
                    res['OI_VIS'][k]['v/wl'] = np.append(res['OI_VIS'][k]['v/wl'], tmp, axis=0)
                    res['OI_VIS'][k]['FLAG'] = np.logical_or(res['OI_VIS'][k]['FLAG'],
                                                             ~np.isfinite(res['OI_VIS'][k]['|V|']))
                    res['OI_VIS'][k]['FLAG'] = np.logical_or(res['OI_VIS'][k]['FLAG'],
                                                             ~np.isfinite(res['OI_VIS'][k]['E|V|']))

                elif any(w):
                    res['OI_VIS'][k] = {'|V|':hdu.data['VISAMP'][w].reshape(w.sum(), -1),
                                        'E|V|':hdu.data['VISAMPERR'][w].reshape(w.sum(), -1),
                                        'PHI':hdu.data['VISPHI'][w].reshape(w.sum(), -1),
                                        'EPHI':hdu.data['VISPHIERR'][w].reshape(w.sum(), -1),
                                        'MJD':hdu.data['MJD'][w],
                                        'u':hdu.data['UCOORD'][w],
                                        'v':hdu.data['VCOORD'][w],
                                        'u/wl': hdu.data['UCOORD'][w][:,None]/
                                               res['WL'][None,:],
                                        'v/wl': hdu.data['VCOORD'][w][:,None]/
                                               res['WL'][None,:],
                                        'FLAG':hdu.data['FLAG'][w].reshape(w.sum(), -1)
                                        }
                if any(w):
                    res['OI_VIS'][k]['B/wl'] = np.sqrt(res['OI_VIS'][k]['u/wl']**2+
                                                        res['OI_VIS'][k]['v/wl']**2)
                    res['OI_VIS'][k]['PA'] = np.angle(res['OI_VIS'][k]['v/wl']+
                                                   1j*res['OI_VIS'][k]['u/wl'], deg=True)

                    res['OI_VIS'][k]['FLAG'] = np.logical_or(res['OI_VIS'][k]['FLAG'],
                                                             ~np.isfinite(res['OI_VIS'][k]['|V|']))
                    res['OI_VIS'][k]['FLAG'] = np.logical_or(res['OI_VIS'][k]['FLAG'],
                                                              ~np.isfinite(res['OI_VIS'][k]['E|V|']))

                    if not binning is None:
                        _w = ~res['OI_VIS'][k]['FLAG']
                        res['OI_VIS'][k]['|V|'],  flag =\
                                                   binOI(res['WL'], _WL,
                                                         res['OI_VIS'][k]['|V|'],
                                                         res['OI_VIS'][k]['FLAG'],
                                                         res['OI_VIS'][k]['E|V|'],
                                                         medFilt=medFilt,
                                                         retFlag=True)
                        res['OI_VIS'][k]['PHI'] = binOI(res['WL'], _WL,
                                                         res['OI_VIS'][k]['PHI'],
                                                         res['OI_VIS'][k]['FLAG'],
                                                         res['OI_VIS'][k]['EPHI'],
                                                         medFilt=medFilt)

                        # -- KLUDGE!
                        res['OI_VIS'][k]['E|V|'] = binOI(res['WL'], _WL,
                                                         res['OI_VIS'][k]['E|V|'],
                                                         res['OI_VIS'][k]['FLAG'],
                                                         res['OI_VIS'][k]['E|V|'],
                                                         medFilt=medFilt)
                        res['OI_VIS'][k]['EPHI'] = binOI(res['WL'], _WL,
                                                         res['OI_VIS'][k]['EPHI'],
                                                         res['OI_VIS'][k]['FLAG'],
                                                         res['OI_VIS'][k]['EPHI'],
                                                         medFilt=medFilt)
                        res['OI_VIS'][k]['FLAG'] = flag

        elif debug and 'EXTNAME' in hdu.header:
            print('DEBUG:', hdu.header['EXTNAME'])
        elif debug:
            print('DEBUG: skipping HDU')

    # -- make sure there is a 1-to-1 correspondance between VIS and VIS2:
    # if 'OI_VIS' in res and 'OI_VIS2' in res:
    #     kz = list(set(list(res['OI_VIS'].keys())+list(res['OI_VIS2'].keys())))
    #     for k in kz:
    #         if not k in res['OI_VIS']:
    #             print(k, 'missing from OI_VIS!')
    #         elif not k in res['OI_VIS2']:
    #             print(k, 'missing from OI_VIS2!')
    #         elif sorted(res['OI_VIS'][k]['MJD']) != sorted(res['OI_VIS2'][k]['MJD']):
    #             print(k, 'mismatched coverage VIS/VIS2!')

    sta2 = list(set(sta2))

    # # -- TEST: removing som MJDs
    # k0 = list(res['OI_VIS2'].keys())[1]
    # #print(res['OI_VIS2'][k0]['MJD'])
    # for k in ['u', 'v', 'MJD']:
    #     res['OI_VIS2'][k0][k] = res['OI_VIS2'][k0][k][2:-2]
    # for k in ['u/wl', 'v/wl', 'FLAG', 'B/wl', 'V2', 'EV2']:
    #     res['OI_VIS2'][k0][k] = res['OI_VIS2'][k0][k][2:-2,:]
    # #print(res['OI_VIS2'][k0]['MJD'])

    M = [] # missing baselines in T3
    for ih, hdu in enumerate(h):
        if 'EXTNAME' in hdu.header and hdu.header['EXTNAME']=='OI_T3' and\
                    hdu.header['INSNAME']==insname:
            #w = hdu.data['TARGET_ID']==targets[targname]
            w = wTarg(hdu, targname, targets)
            if not any(w):
                print('  > \033[33mWARNING\033[0m: no data in OI_T3 [HDU #%d]  for target="%s"/target_id='%(
                            ih, targname), targets[targname])
                continue
            # -- T3 baselines == telescopes pairs
            oiarray = oiarrays[hdu.header['ARRNAME']]
            sta3 = [oiarray[s[0]]+oiarray[s[1]]+oiarray[s[2]] for s in hdu.data['STA_INDEX']]
            # -- limitation: assumes all telescope have same number of char!
            n = len(sta3[0])//3 # number of char per telescope
            for k in set(sta3):
                w = (np.array(sta3)==k)*wTarg(hdu, targname, targets)
                # -- find triangles
                t, s, m = [], [], []
                # -- first baseline
                if k[:2*n] in sta2 or k[:2*n] in M:
                    t.append(k[:2*n])
                    s.append(1)
                elif k[n:2*n]+k[:n] in sta2 or k[:2*n] in M:
                    t.append(k[n:2*n]+k[:n])
                    s.append(-1)
                else:
                    t.append(k[:2*n])
                    s.append(1)
                    M.append(k[:2*n])

                # -- second baseline
                if k[n:] in sta2 or k[n:] in M:
                    t.append(k[n:])
                    s.append(1)
                elif k[2*n:3*n]+k[n:2*n] in sta2 or k[2*n:3*n]+k[n:2*n] in M:
                    t.append(k[2*n:3*n]+k[n:2*n])
                    s.append(-1)
                else:
                    t.append(k[n:])
                    s.append(1)
                    M.append(k[n:])

                # -- third baseline
                if k[2*n:3*n]+k[:n] in sta2 or k[2*n:3*n]+k[:n] in M:
                    t.append(k[2*n:3*n]+k[:n])
                    s.append(1)
                elif k[:n]+k[2*n:3*n] in sta2 or k[:n]+k[2*n:3*n] in M:
                    t.append(k[:n]+k[2*n:3*n])
                    s.append(-1)
                else:
                    t.append(k[2*n:3*n]+k[:n])
                    s.append(1)
                    M.append(k[2*n:3*n]+k[:n])

                if k in res['OI_T3'] and any(w):
                    for k1, k2 in [('T3AMP', 'T3AMP'), ('ET3AMP', 'T3AMPERR'),
                                    ('T3PHI', 'T3PHI'), ('ET3PHI', 'T3PHIERR'),
                                    ('FLAG', 'FLAG')]:
                        res['OI_T3'][k][k1] = np.append(res['OI_T3'][k][k1],
                                                        hdu.data[k2][w].reshape(w.sum(), -1), axis=0)
                    for k1, k2 in [('u1', 'U1COORD'), ('u2', 'U2COORD'),
                                   ('v1', 'V1COORD'), ('v2', 'V2COORD'),
                                   ('MJD', 'MJD')]:
                        res['OI_T3'][k][k1] = np.append(res['OI_T3'][k][k1],
                                                         hdu.data[k2][w])
                    res['OI_T3'][k]['FLAG'] = np.logical_or(res['OI_T3'][k]['FLAG'],
                                                            ~np.isfinite(res['OI_T3'][k]['T3AMP']))
                    res['OI_T3'][k]['FLAG'] = np.logical_or(res['OI_T3'][k]['FLAG'],
                                                            ~np.isfinite(res['OI_T3'][k]['ET3AMP']))
                elif any(w):
                    res['OI_T3'][k] = {'T3AMP':hdu.data['T3AMP'][w].reshape(w.sum(), -1),
                                       'ET3AMP':hdu.data['T3AMPERR'][w].reshape(w.sum(), -1),
                                       'T3PHI':hdu.data['T3PHI'][w].reshape(w.sum(), -1),
                                       'ET3PHI':hdu.data['T3PHIERR'][w].reshape(w.sum(), -1),
                                       'MJD':hdu.data['MJD'][w],
                                       'u1':hdu.data['U1COORD'][w],
                                       'v1':hdu.data['V1COORD'][w],
                                       'u2':hdu.data['U2COORD'][w],
                                       'v2':hdu.data['V2COORD'][w],
                                       'formula': (s, t),
                                       'FLAG':hdu.data['FLAG'][w].reshape(w.sum(), -1)
                                        }
                if any(w):
                    res['OI_T3'][k]['B1'] = np.sqrt(res['OI_T3'][k]['u1']**2+
                                                    res['OI_T3'][k]['v1']**2)
                    res['OI_T3'][k]['B2'] = np.sqrt(res['OI_T3'][k]['u2']**2+
                                                    res['OI_T3'][k]['v2']**2)
                    res['OI_T3'][k]['B3'] = np.sqrt((res['OI_T3'][k]['u1']+res['OI_T3'][k]['u2'])**2+
                                                    (res['OI_T3'][k]['v1']+res['OI_T3'][k]['v2'])**2)
                    bmax = np.maximum(res['OI_T3'][k]['B1'], res['OI_T3'][k]['B2'])
                    bmax = np.maximum(res['OI_T3'][k]['B3'], bmax)
                    bavg = (res['OI_T3'][k]['B1'] +
                            res['OI_T3'][k]['B2'] +
                            res['OI_T3'][k]['B3'])/3

                    res['OI_T3'][k]['Bmax/wl'] = bmax[:,None]/res['WL'][None,:]
                    res['OI_T3'][k]['Bavg/wl'] = bavg[:,None]/res['WL'][None,:]

                    res['OI_T3'][k]['FLAG'] = np.logical_or(res['OI_T3'][k]['FLAG'],
                                                            ~np.isfinite(res['OI_T3'][k]['T3AMP']))
                    res['OI_T3'][k]['FLAG'] = np.logical_or(res['OI_T3'][k]['FLAG'],
                                                            ~np.isfinite(res['OI_T3'][k]['ET3AMP']))
                    if not binning is None:
                        _w = ~res['OI_T3'][k]['FLAG']
                        res['OI_T3'][k]['T3AMP'], flag = \
                                                    binOI(res['WL'], _WL,
                                                          res['OI_T3'][k]['T3AMP'],
                                                          res['OI_T3'][k]['FLAG'],
                                                          res['OI_T3'][k]['ET3AMP'],
                                                          medFilt=medFilt,
                                                          retFlag=True)
                        res['OI_T3'][k]['T3PHI'] = binOI(res['WL'], _WL,
                                                          res['OI_T3'][k]['T3PHI'],
                                                          res['OI_T3'][k]['FLAG'],
                                                          res['OI_T3'][k]['ET3PHI'],
                                                          medFilt=medFilt)

                        # -- KLUDGE!
                        res['OI_T3'][k]['ET3AMP'] = binOI(res['WL'], _WL,
                                                           res['OI_T3'][k]['ET3AMP'],
                                                           res['OI_T3'][k]['FLAG'],
                                                           res['OI_T3'][k]['ET3AMP'],
                                                           medFilt=medFilt)
                        res['OI_T3'][k]['ET3PHI'] = binOI(res['WL'], _WL,
                                                           res['OI_T3'][k]['ET3PHI'],
                                                           res['OI_T3'][k]['FLAG'],
                                                           res['OI_T3'][k]['ET3PHI'],
                                                           medFilt=medFilt)
                        res['OI_T3'][k]['FLAG'] = flag
    key = 'OI_VIS'
    if res['OI_VIS']=={}:
        res.pop('OI_VIS')
        key = 'OI_VIS2'
    if res['OI_VIS2']=={}:
        res.pop('OI_VIS2')
    if res['OI_FLUX']=={}:
        res.pop('OI_FLUX')
    if res['OI_T3']=={}:
        res.pop('OI_T3')
    else:
        if len(M):
            print('    warning: missing baselines', M, 'to define T3')
        # -- match MJDs for T3 computations:
        for k in res['OI_T3'].keys():
            s, t = res['OI_T3'][k]['formula']
            w0, w1, w2 = [], [], []
            # -- add missing baselines
            A = {}
            for m in M:
                if m in t:
                    if t.index(m)==0:
                        u = res['OI_T3'][k]['u1']
                        v = res['OI_T3'][k]['v1']
                    elif t.index(m)==1:
                        u = res['OI_T3'][k]['u2']
                        v = res['OI_T3'][k]['v2']
                    elif t.index(m)==2:
                        u = -res['OI_T3'][k]['u1']-res['OI_T3'][k]['u2']
                        v = -res['OI_T3'][k]['v2']-res['OI_T3'][k]['v2']
                    # -- add data
                    tmp = {}
                    if key=='OI_VIS':
                        _K = ['|V|', 'PHI']
                    else:
                        _K = ['V2']
                    for _k in _K:
                        tmp[_k] = np.zeros((len(res['OI_T3'][k]['MJD']),
                                              len(res['WL'])))
                        tmp['E'+_k] = np.ones((len(res['OI_T3'][k]['MJD']),
                                               len(res['WL'])))
                    tmp['u'], tmp['v'] = u, v
                    tmp['u/wl'] = u[:,None]/res['WL'][None,:]
                    tmp['v/wl'] = v[:,None]/res['WL'][None,:]
                    tmp['B/wl'] = np.sqrt(tmp['u/wl']**2 + tmp['v/wl']**2)
                    tmp['FLAG'] = np.ones((len(res['OI_T3'][k]['MJD']),
                                           len(res['WL'])), dtype=bool)
                    tmp['MJD'] = res['OI_T3'][k]['MJD']
                    A[m] = tmp
            if len(A):
                for m in A:
                    print('    adding fake', m, 'to', key)
                    res[key][m] = A[m]
                    M.remove(m)

            if debug:
                print('DEBUG: OI_T3', k, t, res['OI_T3'][k]['MJD'])
            #try:
            for i, mjd in enumerate(res['OI_T3'][k]['MJD']):
                # check data are within ~10s
                if min(np.abs(res[key][t[0]]['MJD']-mjd))<1e-4:
                    w0.append(np.argmin(np.abs(res[key][t[0]]['MJD']-mjd)))
                else:
                    w0.append(len(res[key][t[0]]['MJD']))
                    #print('WARNING: missing MJD [0]', mjd, k, key, t[0])
                    # -- add fake data for MJD
                    res[key][t[0]]['MJD'] = np.append(res[key][t[0]]['MJD'], mjd)
                    res[key][t[0]]['u'] = np.append(res[key][t[0]]['u'],
                                                   s[0]*res['OI_T3'][k]['u1'][i])
                    res[key][t[0]]['v'] = np.append(res[key][t[0]]['v'],
                                                   s[0]*res['OI_T3'][k]['v1'][i])
                    res[key][t[0]]['u/wl'] = np.append(res[key][t[0]]['u/wl'],
                                                   s[0]*np.array([res['OI_T3'][k]['u1'][i]/
                                                   res['WL']]), axis=0)
                    res[key][t[0]]['v/wl'] = np.append(res[key][t[0]]['v/wl'],
                                                   s[0]*np.array([res['OI_T3'][k]['v1'][i]/
                                                   res['WL']]), axis=0)
                    res[key][t[0]]['PA'] = np.angle(res[key][t[0]]['v/wl']+
                                                 1j*res[key][t[0]]['u/wl'], deg=True)

                    res[key][t[0]]['B/wl'] = np.append(res[key][t[0]]['B/wl'],
                                                 np.array([np.sqrt(res['OI_T3'][k]['u1'][i]**2+
                                                         res['OI_T3'][k]['v1'][i]**2)/
                                                 res['WL']]), axis=0)
                    res[key][t[0]]['FLAG'] = np.append(res[key][t[0]]['FLAG'],
                                                np.array([np.ones(len(res['WL']), dtype=bool)]), axis=0)

                    if key=='OI_VIS':
                        res[key][t[0]]['|V|'] = np.append(res[key][t[0]]['|V|'],
                                                np.array([res['WL']*0]), axis=0)
                        res[key][t[0]]['E|V|'] = np.append(res[key][t[0]]['E|V|'],
                                                 np.array([res['WL']*0+1]), axis=0)
                        res[key][t[0]]['PHI'] = np.append(res[key][t[0]]['PHI'],
                                                np.array([res['WL']*0]), axis=0)
                        res[key][t[0]]['EPHI'] = np.append(res[key][t[0]]['EPHI'],
                                                np.array([res['WL']*0+1]), axis=0)
                    else:
                        res[key][t[0]]['V2'] = np.append(res[key][t[0]]['V2'],
                                                np.array([res['WL']*0]), axis=0)
                        res[key][t[0]]['EV2'] = np.append(res[key][t[0]]['EV2'],
                                                np.array([res['WL']*0+1]), axis=0)
                if min(np.abs(res[key][t[1]]['MJD']-mjd))<1e-4:
                    w1.append(np.argmin(np.abs(res[key][t[1]]['MJD']-mjd)))
                else:
                    w1.append(len(res[key][t[1]]['MJD']))
                    #print('WARNING: missing MJD [1]', mjd, k, key, t[1])
                    # -- add fake data for MJD
                    res[key][t[1]]['MJD'] = np.append(res[key][t[1]]['MJD'], mjd)
                    res[key][t[1]]['u'] = np.append(res[key][t[1]]['u'],
                                                   s[1]*res['OI_T3'][k]['u2'][i])
                    res[key][t[1]]['v'] = np.append(res[key][t[1]]['v'],
                                                   s[1]*res['OI_T3'][k]['v2'][i])
                    res[key][t[1]]['u/wl'] = np.append(res[key][t[1]]['u/wl'],
                                                   s[1]*np.array([res['OI_T3'][k]['u2'][i]/
                                                   res['WL']]), axis=0)
                    res[key][t[1]]['v/wl'] = np.append(res[key][t[1]]['v/wl'],
                                                   s[1]*np.array([res['OI_T3'][k]['v2'][i]/
                                                   res['WL']]), axis=0)
                    res[key][t[1]]['PA'] = np.angle(res[key][t[1]]['v/wl']+
                                                 1j*res[key][t[1]]['u/wl'], deg=True)

                    res[key][t[1]]['B/wl'] = np.append(res[key][t[1]]['B/wl'],
                                                 np.array([np.sqrt(res['OI_T3'][k]['u2'][i]**2+
                                                         res['OI_T3'][k]['v2'][i]**2)/
                                                 res['WL']]), axis=0)
                    res[key][t[1]]['FLAG'] = np.append(res[key][t[1]]['FLAG'],
                                                np.array([np.ones(len(res['WL']), dtype=bool)]), axis=0)
                    if key=='OI_VIS':
                        res[key][t[1]]['|V|'] = np.append(res[key][t[1]]['|V|'],
                                                np.array([res['WL']*0]), axis=0)
                        res[key][t[1]]['E|V|'] = np.append(res[key][t[1]]['E|V|'],
                                                 np.array([res['WL']*0+1]), axis=0)
                        res[key][t[1]]['PHI'] = np.append(res[key][t[1]]['PHI'],
                                                np.array([res['WL']*0]), axis=0)
                        res[key][t[1]]['EPHI'] = np.append(res[key][t[1]]['EPHI'],
                                                np.array([res['WL']*0+1]), axis=0)
                    else:
                        res[key][t[1]]['V2'] = np.append(res[key][t[1]]['V2'],
                                                np.array([res['WL']*0]), axis=0)
                        res[key][t[1]]['EV2'] = np.append(res[key][t[1]]['EV2'],
                                                np.array([res['WL']*0+1]), axis=0)
                if min(np.abs(res[key][t[2]]['MJD']-mjd))<1e-4:
                    w2.append(np.argmin(np.abs(res[key][t[2]]['MJD']-mjd)))
                else:
                    w2.append(len(res[key][t[2]]['MJD']))
                    #print('WARNING: missing MJD [2]', mjd, k, key, t[2])
                    # -- add fake data for MJD
                    res[key][t[2]]['MJD'] = np.append(res[key][t[2]]['MJD'], mjd)
                    res[key][t[2]]['u'] = np.append(res[key][t[2]]['u'],
                                                   -s[2]*res['OI_T3'][k]['u1'][i]
                                                   -s[2]*res['OI_T3'][k]['u2'][i])
                    res[key][t[2]]['v'] = np.append(res[key][t[2]]['v'],
                                                   -s[2]*res['OI_T3'][k]['v1'][i]
                                                   -s[2]*res['OI_T3'][k]['v2'][i])
                    res[key][t[2]]['u/wl'] = np.append(res[key][t[2]]['u/wl'],
                                                   s[2]*np.array([(-res['OI_T3'][k]['u1'][i]
                                                                   -res['OI_T3'][k]['u2'][i])/
                                                   res['WL']]), axis=0)
                    res[key][t[2]]['v/wl'] = np.append(res[key][t[2]]['v/wl'],
                                                   np.array([(-res['OI_T3'][k]['v1'][i]-res['OI_T3'][k]['v2'][i])/
                                                   res['WL']]), axis=0)
                    res[key][t[2]]['PA'] = np.angle(res[key][t[2]]['v/wl']+
                                                 1j*res[key][t[2]]['u/wl'], deg=True)

                    res[key][t[2]]['B/wl'] = np.append(res[key][t[2]]['B/wl'],
                                                 np.array([np.sqrt((res['OI_T3'][k]['u1'][i]+res['OI_T3'][k]['u2'][i])**2+
                                                         (res['OI_T3'][k]['v1'][i]+res['OI_T3'][k]['v2'][i])**2)/
                                                 res['WL']]), axis=0)
                    res[key][t[2]]['FLAG'] = np.append(res[key][t[2]]['FLAG'],
                                                np.array([np.ones(len(res['WL']), dtype=bool)]), axis=0)
                    if key=='OI_VIS':
                        res[key][t[2]]['|V|'] = np.append(res[key][t[2]]['|V|'],
                                                np.array([res['WL']*0]), axis=0)
                        res[key][t[2]]['E|V|'] = np.append(res[key][t[2]]['E|V|'],
                                                np.array([res['WL']*0+1]), axis=0)
                        res[key][t[2]]['PHI'] = np.append(res[key][t[2]]['PHI'],
                                                np.array([res['WL']*0]), axis=0)
                        res[key][t[2]]['EPHI'] = np.append(res[key][t[2]]['EPHI'],
                                                np.array([res['WL']*0+1]), axis=0)
                    else:
                        res[key][t[2]]['V2'] = np.append(res[key][t[2]]['V2'],
                                                np.array([res['WL']*0]), axis=0)
                        res[key][t[2]]['EV2'] = np.append(res[key][t[2]]['EV2'],
                                                np.array([res['WL']*0+1]), axis=0)

            res['OI_T3'][k]['formula'] = [s, t, w0, w1, w2]
            # except:
            #     print('warning! triplet', k,
            #           'has no formula in', res[key].keys())
            #     res['OI_T3'][k]['formula'] = None
    if 'OI_FLUX' in res:
        res['telescopes'] = sorted(list(res['OI_FLUX'].keys()))
    else:
        res['telescopes'] = []
        for k in res[key].keys():
            res['telescopes'].append(k[:len(k)//2])
            res['telescopes'].append(k[len(k)//2:])
        res['telescopes'] = sorted(list(set(res['telescopes'])))
    res['baselines'] = sorted(list(res[key].keys()))
    if 'OI_T3' in res.keys():
        res['triangles'] = sorted(list(res['OI_T3'].keys()))
    else:
        res['triangles'] = []

    if not 'TELLURICS' in res.keys():
        res['TELLURICS'] = np.ones(res['WL'].shape)
    if not tellurics is None:
        # -- forcing tellurics to given vector
        res['TELLURICS'] = tellurics
        if not binning is None and len(tellurics)==len(_WL):
            res['TELLURICS'] = _binVec(res['WL'], _WL, tellurics)

    if 'OI_FLUX' in res.keys():
        for k in res['OI_FLUX'].keys():
            # -- raw flux
            res['OI_FLUX'][k]['RFLUX'] = res['OI_FLUX'][k]['FLUX'].copy()
            # -- corrected from tellurics
            res['OI_FLUX'][k]['FLUX'] /= res['TELLURICS'][None,:]

    if not medFilt is None:
        if type(medFilt) == int:
            kernel_size = 2*(medFilt//2)+1
        else:
            kernel_size = None
        res = medianFilt(res, kernel_size)

    confperMJD = {}
    for l in filter(lambda x: x in ['OI_VIS', 'OI_VIS2', 'OI_T3', 'OI_FLUX'], res.keys()):
        for k in res[l].keys():
            for mjd in res[l][k]['MJD']:
                if not mjd in confperMJD.keys():
                    confperMJD[mjd] = [k]
                else:
                    if not k in confperMJD[mjd]:
                        confperMJD[mjd].append(k)
    res['configurations per MJD'] = confperMJD

    if verbose:
        mjd = []
        for e in ['OI_VIS2', 'OI_VIS', 'OI_T3', 'OI_FLUX']:
            if e in res.keys():
                for k in res[e].keys():
                    mjd.extend(list(res[e][k]['MJD']))
        mjd = np.array(sorted(set(mjd)))
        #print('  > MJD:', sorted(set(mjd)))
        print('  > MJD:', mjd.shape, '[', min(mjd), '..', max(mjd), ']')
        print('  >', '-'.join(res['telescopes']), end=' | ')
        print('WL:', res['WL'].shape, '[', round(np.min(res['WL']), 3), '..',
              round(np.max(res['WL']), 3),
              '] um (R~%.0f)'%(np.mean(res['WL']/res['dWL'])),
              end=' ')
        if not binning is None:
            print('(binned by x%d)'%binning, end=' ')
        #print(sorted(list(filter(lambda x: x.startswith('OI_'), res.keys()))),
        #            end=' | ')
        Kz = sorted(list(filter(lambda x: x.startswith('OI_'), res.keys())))
        print(dict(zip(Kz, [len(res[k].keys()) for k in Kz])), end=' | ')

        print('| TELLURICS:', res['TELLURICS'].min()<1)
        # print('  >', 'telescopes:', res['telescopes'],
        #       'baselines:', res['baselines'],
        #       'triangles:', res['triangles'])

    return res

def wTarg(hdu, targname, targets):
    if type(targets[targname]) == list:
        return np.array([x in targets[targname] for x in hdu.data['TARGET_ID']])
    else:
        return hdu.data['TARGET_ID']==targets[targname]


def binOI(_wl, WL, T, F, E=None, medFilt=None, retFlag=False):
    """
    _wl: new WL vector
    WL: actual WL vector
    T: data table (2D)
    F: flag table (2D)
    """
    res = np.zeros((T.shape[0], len(_wl)))
    flag = np.zeros((T.shape[0], len(_wl)), dtype=bool)
    for i in range(T.shape[0]):
        w = ~F[i,:]
        flag[i,:] = _binVec(_wl, WL, np.float_(F[i,:]))>0.5
        if E is None:
            res[i,:] = _binVec(_wl, WL[w], T[i,:][w], medFilt=medFilt)
        else:
            try:
                res[i,:] = _binVec(_wl, WL[w], T[i,:][w], E=E[i,:][w], medFilt=medFilt)
            except:
                res[i,:] = np.nan
    if retFlag:
        return res, flag
    return res

def _binVec(x, X, Y, E=None, medFilt=None):
    """
    bin Y(X) with new x. E is optional error bars (wor weighting)
    """
    dx = np.median(np.diff(x))
    if E is None:
        E = np.ones(len(Y))

    # -- X can be irregular...
    y = np.zeros(len(x))
    for i,x0 in enumerate(x):
        k = np.exp(-(X-x0)**2/(0.6*dx)**2)
        #print(k.shape, Y.shape, E.shape)
        y[i] = np.sum(k*Y/E)/np.sum(k/E)
    return y

    # -- kernel of FWHM dx
    k = np.exp(-(X-np.mean(X))**2/(0.6*dx)**2)
    #k = np.float_(np.abs(X-np.mean(X))<=dx/2)
    k /= np.sum(k)
    W = np.convolve(1/E, k, 'same')
    s = np.argsort(X)

    if not medFilt is None:
        return np.interp(x, X[s], np.convolve(scipy.signal.medfilt(Y/E,
                                        kernel_size=medFilt)[s], k, 'same')/W[s])
    else:
        return np.interp(x, X[s], np.convolve(Y/E, k, "same")[s]/W[s])

def mergeOI(OI, collapse=False, groups=None, verbose=True, debug=False):
    """
    takes OI, a list of oifits files readouts (from loadOI), and merge them into
    a smaller number of entities based with same spectral coverage

    collapse=True -> all telescope / baseline / triangle in a single dict for
        faster computation

    groups=[...] list sub-strings for grouping insnames

    TODO: how polarisation in handled?
    """
    # -- create unique identifier for each setups
    setups = [oi['insname']+str(oi['WL']) for oi in OI]
    merged = [] # list of unique setup which have been merged so far
    master = [] # same length as OI, True if element hold merged data for the setup
    res = [] # result
    for i, oi in enumerate(OI):
        if debug:
            print(i, setups[i])
        if not setups[i] in merged:
            # -- this will hold the data for this setup
            merged.append(setups[i])
            master.append(True)
            if debug:
                print('super oi')
            res.append(copy.deepcopy(oi))
            # -- filter in place
            for l in [r for r in res[-1].keys() if r.startswith('OI_')]:
                for k in res[-1][l].keys():
                    for t in res[-1][l][k].keys():
                        if t.startswith('E') and t[1:] in res[-1][l][k] and 'fit' in res[-1]:
                            # -- errors -> allow editing based on 'fit'
                            res[-1][l][k][t] = _filtErr(t[1:], res[-1][l][k], res[-1]['fit'], debug=0)
                        elif t == 'FLAG' and 'fit' in res[-1]:
                            # -- flags -> allow editing based on 'fit'
                            res[-1][l][k][t] = _filtFlag(res[-1][l][k], res[-1]['fit'], debug=0)
            continue # exit for loop, nothing else to do
        else:
            # -- find data holder for this setup
            #i0 = np.argmax([setups[j]==setups[i] and master[j] for j in range(i)])
            i0 = merged.index(setups[i])
            master.append(False)
            # -- start merging with filename
            res[i0]['filename'] += ';'+oi['filename']

        if debug:
            print('  will be merged with', i0)
            print('  merging...')
        # -- extensions to be merged:
        exts = ['OI_VIS', 'OI_VIS2', 'OI_T3', 'OI_FLUX']
        for l in filter(lambda e: e in exts, oi.keys()):
            if not l in res[i0].keys():
                # -- extension not present, using the one from oi
                res[i0][l] = copy.deepcopy(oi[l])
                if debug:
                    print('    ', i0, 'does not have', l, '!')

                # -- no additional merging needed
                dataMerge = False
            else:
                dataMerge = True

            # -- merge list of tel/base/tri per MJDs:
            for mjd in oi['configurations per MJD']:
                if not mjd in res[i0]['configurations per MJD']:
                    res[i0]['configurations per MJD'][mjd] = \
                         oi['configurations per MJD'][mjd]
                else:
                    for k in oi['configurations per MJD'][mjd]:
                        if not k in res[i0]['configurations per MJD'][mjd]:
                            res[i0]['configurations per MJD'][mjd].append(k)
            if not dataMerge:
                continue

            # -- merge data in the extension:
            for k in oi[l].keys():
                # -- for each telescpe / baseline / triangle
                if not k in res[i0][l].keys():
                    # -- unknown telescope / baseline / triangle
                    # -- just add it in the the dict
                    res[i0][l][k] = copy.deepcopy(oi[l][k])
                    if 'FLUX' in l:
                        res[i0]['telescopes'].append(k)
                    elif 'VIS' in l:
                        res[i0]['baselines'].append(k)
                    elif 'T3' in l:
                        res[i0]['triangles'].append(k)
                    if debug:
                        print('    adding', k, 'to', l, 'in', i0)
                    # -- filter in place
                    for t in res[i0][l][k].keys():
                        if t.startswith('E') and t[1:] in res[i0][l][k] and 'fit' in res[i0]:
                            # -- errors -> allow editing
                            res[i0][l][k][t] = _filtErr(t[1:], res[i0][l][k], res[i0]['fit'])
                        elif t == 'FLAG' and 'fit' in res[i0]:
                            # -- flags -> editing based on errors
                            res[i0][l][k][t] = _filtFlag(res[i0][l][k], res[i0]['fit'])
                else:
                    # -- merging data (most complicated case)
                    # -- ext1 are scalar
                    # -- ext2 are vector of length WL
                    if l=='OI_FLUX':
                        ext1 = ['MJD']
                        ext2 = ['FLUX', 'EFLUX', 'FLAG', 'RFLUX']
                    elif l=='OI_VIS2':
                        ext1 = ['u', 'v', 'MJD']
                        ext2 = ['V2', 'EV2', 'FLAG', 'u/wl', 'v/wl', 'B/wl', 'PA']
                    elif l=='OI_VIS':
                        ext1 = ['u', 'v', 'MJD']
                        ext2 = ['|V|', 'E|V|', 'PHI', 'EPHI', 'FLAG', 'u/wl', 'v/wl', 'B/wl', 'PA']
                    if l=='OI_T3':
                        ext1 = ['u1', 'v1', 'u2', 'v2', 'MJD', 'B1', 'B2', 'B3']
                        ext2 = ['T3AMP', 'ET3AMP', 'T3PHI', 'ET3PHI',
                                'FLAG', 'Bmax/wl', 'Bavg/wl']
                    if debug:
                        print(l, k, res[i0][l][k].keys())
                    for t in ext1:
                        # -- append len(MJDs) data
                            res[i0][l][k][t] = np.append(res[i0][l][k][t], oi[l][k][t])
                    for t in ext2:
                        # -- append (len(MJDs),len(WL)) data
                        s1 = res[i0][l][k][t].shape # = (len(MJDs),len(WL))
                        s2 = oi[l][k][t].shape # = (len(MJDs),len(WL))
                        if t.startswith('E') and t[1:] in oi[l][k] and 'fit' in oi:
                            # -- errors -> allow editing
                            tmp = _filtErr(t[1:], oi[l][k], oi['fit'])
                            #print(oi[l][k][t], '->', tmp)
                        elif t == 'FLAG' and 'fit' in oi:
                            # -- flags -> editing based on errors
                            tmp = _filtFlag(oi[l][k], oi['fit'])
                        else:
                            tmp = oi[l][k][t]
                        res[i0][l][k][t] = np.append(res[i0][l][k][t], tmp)
                        res[i0][l][k][t] = res[i0][l][k][t].reshape(s1[0]+s2[0], s1[1])

    for r in res:
        for k in ['telescopes', 'baselines', 'triangles']:
            if k in r:
                r[k] = list(set(r[k]))

    #-- make sure there is a 1-to-1 correspondance between VIS and VIS2:
    for r in res:
        if 'OI_VIS' in r and 'OI_VIS2' in r:
            kz = list(set(list(r['OI_VIS'].keys())+list(r['OI_VIS2'].keys())))
            #print(kz)
            for k in kz:
                if not k in r['OI_VIS']:
                    #print(k, 'missing from OI_VIS!')
                    r['OI_VIS'][k]={}
                    for x in ['MJD', 'u', 'v', 'u/wl', 'v/wl', 'B/wl', 'PA',
                             'FLAG']:
                        r['OI_VIS'][k][x] = r['OI_VIS2'][k][x].copy()
                    r['OI_VIS'][k]['FLAG'] = np.logical_or(r['OI_VIS'][k]['FLAG'],
                                                            True)
                    r['OI_VIS'][k]['|V|'] = 1+0*r['OI_VIS2'][k]['V2']
                    r['OI_VIS'][k]['E|V|'] = 1+0*r['OI_VIS2'][k]['V2']
                    r['OI_VIS'][k]['PHI'] = 1+0*r['OI_VIS2'][k]['V2']
                    r['OI_VIS'][k]['EPHI'] = 1+0*r['OI_VIS2'][k]['V2']
                elif not k in r['OI_VIS2']: # -- NOT important...
                    #print(k, 'missing from OI_VIS2!')
                    r['OI_VIS2'][k]={}
                    for x in ['MJD', 'u', 'v', 'u/wl', 'v/wl', 'B/wl', 'PA',
                             'FLAG']:
                        r['OI_VIS2'][k][x] = r['OI_VIS'][k][x].copy()
                    #r['OI_VIS2'][k]['FLAG'] += True
                    r['OI_VIS2'][k]['FLAG'] = np.logical_or(r['OI_VIS2'][k]['FLAG'],
                                                            True)

                    r['OI_VIS2'][k]['V2'] = 1+0*r['OI_VIS'][k]['|V|']
                    r['OI_VIS2'][k]['EV2'] = 1+0*r['OI_VIS'][k]['|V|']
                mjd = list(set(list(r['OI_VIS'][k]['MJD'])+list(r['OI_VIS2'][k]['MJD'])))
                if len(r['OI_VIS'][k]['MJD']) < len(mjd):
                    #print(k, 'VIS is missing data! (%d/%d)'%(len(r['OI_VIS'][k]['MJD']), len(mjd)))
                    w = [x not in r['OI_VIS'][k]['MJD'] for x in r['OI_VIS2'][k]['MJD']]
                    w = np.array(w)

                    for x in ['MJD', 'u', 'v']:
                        r['OI_VIS'][k][x] = np.append(r['OI_VIS'][k][x],
                                                      r['OI_VIS2'][k][x][w])

                    for x in ['u/wl', 'v/wl', 'B/wl', 'FLAG', 'PA',
                              '|V|', 'E|V|', 'PHI', 'EPHI']:
                        if x in r['OI_VIS2'][k]:
                            r['OI_VIS'][k][x] = np.concatenate((r['OI_VIS'][k][x],
                                                                r['OI_VIS2'][k][x][w].reshape(w.sum(), -1)))
                        else:
                            r['OI_VIS'][k][x] = np.concatenate((r['OI_VIS'][k][x],
                                                                1+0*r['OI_VIS2'][k]['V2'][w].reshape(w.sum(), -1)))
                    r['OI_VIS'][k]['FLAG'][-sum(w):,:] = np.logical_or(
                                        r['OI_VIS'][k]['FLAG'][-sum(w):,:],True)

                    # -- check:
                    # for x in r['OI_VIS'][k]:
                    #     print(x, r['OI_VIS'][k][x].shape, end=' ')
                    #     if x in r['OI_VIS2'][k]:
                    #         print(r['OI_VIS2'][k][x].shape)
                    #     else:
                    #         print(r['OI_VIS2'][k]['V2'].shape)

                if len(r['OI_VIS2'][k]['MJD']) != len(mjd):
                    #print(k, 'VIS2 is missing data! (%d/%d)'%(len(mjd)-len(r['OI_VIS2'][k]['MJD']), len(mjd)))
                    # -- TODO: copy what is above!
                    w = [x not in r['OI_VIS2'][k]['MJD'] for x in r['OI_VIS'][k]['MJD']]
                    w = np.array(w)

                    for x in ['MJD', 'u', 'v']:
                        r['OI_VIS2'][k][x] = np.append(r['OI_VIS2'][k][x],
                                                      r['OI_VIS'][k][x][w])

                    for x in ['u/wl', 'v/wl', 'B/wl', 'FLAG', 'PA',
                              'V2', 'EV2']:
                        if x in r['OI_VIS'][k]:
                            r['OI_VIS2'][k][x] = np.concatenate((r['OI_VIS2'][k][x],
                                                                r['OI_VIS'][k][x][w].reshape(w.sum(), -1)))
                        else:
                            r['OI_VIS2'][k][x] = np.concatenate((r['OI_VIS2'][k][x],
                                                                1+0*r['OI_VIS'][k]['|V|'][w].reshape(w.sum(), -1)))
                    r['OI_VIS2'][k]['FLAG'][-sum(w):,:] = np.logical_or(
                                        r['OI_VIS2'][k]['FLAG'][-sum(w):,:], True)
    # -- special case for T3 formulas
    # -- match MJDs for T3 computations:
    for r in res:
        if 'OI_T3' in r.keys():
            rmkey = []
            for k in r['OI_T3'].keys():
                s, t, w0, w1, w2 = r['OI_T3'][k]['formula']
                _w0, _w1, _w2 = [], [], []
                if 'OI_VIS' in r.keys() and t[0] in r['OI_VIS'] and \
                        t[1] in r['OI_VIS'] and t[2] in r['OI_VIS']:
                    key = 'OI_VIS'
                elif 'OI_VIS2' in r.keys() and t[0] in r['OI_VIS2'] and \
                        t[1] in r['OI_VIS2'] and t[2] in r['OI_VIS2']:
                    key = 'OI_VIS2'
                else:
                    # I Should never arrive here!!!
                    key = None
                try:
                    for mjd in r['OI_T3'][k]['MJD']:
                        _w0.append(np.argmin(np.abs(r[key][t[0]]['MJD']-mjd)))
                        _w1.append(np.argmin(np.abs(r[key][t[1]]['MJD']-mjd)))
                        _w2.append(np.argmin(np.abs(r[key][t[2]]['MJD']-mjd)))
                    r['OI_T3'][k]['formula'] = [s, t, _w0, _w1, _w2]
                except:
                    tmp = sorted(list(r[key].keys()))
                    print('OOPS: cannot compute T3 formula!!!')
                    print(k, t, t[0] in tmp, t[1] in tmp, t[2] in tmp, key, tmp)
                    rmkey.append(k)
                    r['OI_T3'][k]['formula'] = None
            for k in rmkey:
               r['OI_T3'].pop(k)

    # -- keep only master oi's, which contains merged data:
    if verbose:
        print('mergeOI:', len(OI), 'data files merged into', len(res), end=' ')
        if len(res)>1:
            print('dictionnaries with unique setups:')
        else:
            print('dictionnary with a unique setup:')
        for i, m in enumerate(res):
            print(' [%d]'%i, m['insname'], ' %d wavelengths:'%len(m['WL']),
                    m['WL'][0], '..', m['WL'][-1], 'um', end=' ')
            print(m['baselines'], end='\n  ')
            print('\n  '.join(m['filename'].split(';')))
    if collapse:
        # -- all baselines in a single key (faster computations)
        res = _allInOneOI(res, verbose=verbose, debug=debug)

    # -- keep fitting context, not need to keep error-based ones, except DPHI!
    for r in res:
        if 'fit' in r:
            tmp = {}
            for k in r['fit'].keys():
                # -- for differential quantities, this are globally defined
                for p in ['DPHI', 'NFLUX']:
                    if type(r['fit'][k]) is dict and p in r['fit'][k].keys():
                        if k in tmp:
                            if p in tmp[k] and tmp[k][p]!=r['fit'][k][p]:
                                print('mergeOI: WARNING, merging cannot merge "'+
                                      k+'" for "'+p+'"')
                            tmp[k][p] = r['fit'][k][p]
                        else:
                            tmp[k] = {p:r['fit'][k][p]}
            r['fit'] = {k:r['fit'][k] for k in ['obs', 'wl ranges',
                                                'continuum ranges', 'prior']
                        if k in r['fit']}
            r['fit'].update(tmp)

    return res

def _filtErr(t, ext, filt, debug=False):
    """
    t: name of the observable (e.g. 'T3PHI', 'V2', etc)
    ext: dictionnary containing the data (OI extension)
    filt: what to apply to data ('fit' dict from OIDATA)
    """
    # -- original errors:
    err = ext['E'+t].copy()

    # == this is now in oimodels.computeNormFluxOI
    # -- this is a bit of a Kludge => impacts badly bootstrapping!!!
    # if t=='FLUX' and 'min error' in filt.keys() and 'NFLUX' in filt['min error']:
    #     filt['min error']['FLUX'] = filt['min error']['NFLUX']*np.median(ext['FLUX'])
    #
    # # -- this one is correct
    # if t=='FLUX' and 'mult error' in filt.keys() and 'NFLUX' in filt['mult error']:
    #     filt['mult error']['FLUX'] = filt['mult error']['NFLUX']

    if 'mult error' in filt.keys() and t in filt['mult error'].keys():
        if debug:
            print('mult error:', t, end=' ')
        err *= filt['mult error'][t]

    if 'min error' in filt.keys() and t in filt['min error'].keys():
        if debug:
            print('min error:', t, end=' ')
            print(np.sum(err<filt['min error'][t]), '/', err.size)
        err = np.maximum(filt['min error'][t], err)

    if 'min relative error' in filt.keys() and t in filt['min relative error'].keys():
        if debug:
            print('min relative error:', t, end=' ')
            print(np.sum(err<filt['min relative error'][t]*np.abs(ext[t])),
                    '/', err.size)
        err = np.maximum(filt['min relative error'][t]*np.abs(ext[t]), err)
    return err

def _filtFlag(ext, filt, debug=False):
    """
    ext: dictionnary containing data (OI extension)
    """
    flag = ext['FLAG'].copy()
    if not 'max error' in filt and not 'max relative error' in filt:
        return flag

    # -- this is a bit of a Kludge :S
    if 'FLUX' in ext and 'max error' in filt.keys() and 'NFLUX' in filt['max error']:
        filt['max error']['FLUX'] = filt['max error']['NFLUX']*ext['FLUX'].mean()

    if 'max error' in filt:
        for k in filt['max error']:
            if k == 'DPHI':
                k = 'PHI'
            if k in filt['max error'] and 'E'+k in ext:
                if debug:
                    print('flag: max', k, filt['max error'])
                flag += ext['E'+k]>=filt['max error'][k]
    if 'max relative error' in filt:
        for k in filt['max relative error']:
            if k in filt['max relative error'] and 'E'+k in ext:
                if debug:
                    print('flag: max relative', k)
                flag += ext['E'+k]>=filt['max relative error'][k]*np.abs(ext[k])
    return flag

def _allInOneOI(oi, verbose=False, debug=False):
    """
    allInOne:
    - averages fluxes per MJD
    - puts all baselines / triangles in same dict

    """
    if type(oi)==list:
        return [_allInOneOI(o, verbose=verbose, debug=debug) for o in oi]

    if 'OI_FLUX' in oi:
        fluxes = {}
        weights = {}
        names = {}
        for k in oi['OI_FLUX'].keys():
            for j,mjd in enumerate(oi['OI_FLUX'][k]['MJD']):
                mask = ~oi['OI_FLUX'][k]['FLAG'][j,:]
                if not mjd in fluxes:
                    fluxes[mjd] = np.zeros(len(oi['WL']))
                    weights[mjd] = np.zeros(len(oi['WL']))
                    names[mjd] = ''
                fluxes[mjd][mask] += oi['OI_FLUX'][k]['FLUX'][j,mask]/\
                        oi['OI_FLUX'][k]['EFLUX'][j,mask]
                weights[mjd][mask] += 1/oi['OI_FLUX'][k]['EFLUX'][j,mask]
                names[mjd] += k
        flags = {}
        efluxes = {}
        for mjd in fluxes.keys():
            mask = weights[mjd]>0
            fluxes[mjd][mask] /= weights[mjd][mask]
            flags[mjd] = ~mask
            efluxes[mjd] = np.zeros(len(oi['WL']))
            efluxes[mjd][mask] = 1/weights[mjd][mask]
        oi['OI_FLUX']['all'] = {
            'FLUX': np.array([fluxes[mjd] for mjd in fluxes.keys()]),
            'EFLUX': np.array([efluxes[mjd] for mjd in fluxes.keys()]),
            'FLAG': np.array([flags[mjd] for mjd in fluxes.keys()]),
            'NAME': np.array([names[mjd] for mjd in fluxes.keys()]),
            'MJD': np.array(list(fluxes.keys())),
            }

    for e in filter(lambda x: x in oi.keys(), ['OI_VIS', 'OI_VIS2', 'OI_T3']):
        tmp = {'NAME':[]}
        for k in filter(lambda x: x!='all', oi[e].keys()): # each Tel/B/Tri
            tmp['NAME'].extend([k for i in range(len(oi[e][k]['MJD']))])
            for d in oi[e][k].keys(): # each data type
                if not d in tmp:
                    if type(oi[e][k][d])==list:
                        tmp[d] = [tuple(oi[e][k][d])]*len(oi[e][k]['MJD'])
                    else:
                        tmp[d] = oi[e][k][d]
                else:
                    if type(oi[e][k][d])==list:
                        tmp[d].extend([tuple(oi[e][k][d])]*len(oi[e][k]['MJD']))
                    elif type(oi[e][k][d])==np.ndarray:
                        if oi[e][k][d].ndim==1:
                            tmp[d] = np.append(tmp[d], oi[e][k][d])
                        elif oi[e][k][d].ndim==2:
                            tmp[d] = np.append(tmp[d], oi[e][k][d], axis=0)
                    else:
                        print('allInOneOI warning: unknow data', e, d)
        tmp['NAME'] = np.array(tmp['NAME'])
        oi[e]['all'] = tmp

    if 'OI_T3' in oi:
        key = 'OI_VIS'
        if not key in oi:
            key = 'OI_VIS2'
        # -- recompute formula for T3
        _w0, _w1, _w2 = [], [], []
        _s0, _s1, _s2 = [], [], []
        for i,mjd in enumerate(oi['OI_T3']['all']['MJD']):
            s, t, w0, w1, w2 = oi['OI_T3']['all']['formula'][i]
            _s0.append(s[0])
            _s1.append(s[1])
            _s2.append(s[2])
            _w0.append(np.argmin(abs(oi[key]['all']['MJD']-mjd)+(oi[key]['all']['NAME']!=t[0])))
            _w1.append(np.argmin(abs(oi[key]['all']['MJD']-mjd)+(oi[key]['all']['NAME']!=t[1])))
            _w2.append(np.argmin(abs(oi[key]['all']['MJD']-mjd)+(oi[key]['all']['NAME']!=t[2])))
        s = np.array(_s0), np.array(_s1), np.array(_s2)
        oi['OI_T3']['all']['formula'] = [s, ('all', 'all', 'all'), _w0, _w1, _w2]
    for e in filter(lambda x: x.startswith('OI_'), oi.keys()):
        for k in list(oi[e].keys()):
            if k!='all':
                oi[e].pop(k)
    oi['telescopes'] = ['all']
    oi['baselines'] = ['all']
    oi['triangles'] = ['all']
    return oi

def medianFilt(oi, kernel_size=None):
    """
    kernel_size is the half width
    """
    if type(oi) == list:
        return [medianFilt(o, kernel_size=kernel_size) for o in oi]

    if 'OI_FLUX' in oi.keys():
        # -- make sure the tellurics are handled properly
        if 'TELLURICS' in oi.keys():
            t = oi['TELLURICS']
        else:
            t = np.ones(np.len(oi['WL']))
        for k in oi['OI_FLUX'].keys():
            for i in range(len(oi['OI_FLUX'][k]['MJD'])):
                mask = ~oi['OI_FLUX'][k]['FLAG'][i,:]
                oi['OI_FLUX'][k]['FLUX'][i,mask] = scipy.signal.medfilt(
                    oi['OI_FLUX'][k]['FLUX'][i,mask]/t[mask],
                    kernel_size=kernel_size)*t[mask]
                oi['OI_FLUX'][k]['EFLUX'][i,mask] /= np.sqrt(kernel_size)
    if 'OI_VIS' in oi.keys():
        for k in oi['OI_VIS'].keys():
            for i in range(len(oi['OI_VIS'][k]['MJD'])):
                mask = ~oi['OI_VIS'][k]['FLAG'][i,:]
                oi['OI_VIS'][k]['|V|'][i,mask] = scipy.signal.medfilt(
                    oi['OI_VIS'][k]['|V|'][i,mask], kernel_size=kernel_size)
                oi['OI_VIS'][k]['E|V|'][i,mask] /= np.sqrt(kernel_size)

                oi['OI_VIS'][k]['PHI'][i,mask] = scipy.signal.medfilt(
                    oi['OI_VIS'][k]['PHI'][i,mask], kernel_size=kernel_size)
                oi['OI_VIS'][k]['EPHI'][i,mask] /= np.sqrt(kernel_size)

    if 'OI_VIS2' in oi.keys():
        for k in oi['OI_VIS2'].keys():
            for i in range(len(oi['OI_VIS2'][k]['MJD'])):
                mask = ~oi['OI_VIS2'][k]['FLAG'][i,:]
                oi['OI_VIS2'][k]['V2'][i,mask] = scipy.signal.medfilt(
                    oi['OI_VIS2'][k]['V2'][i,mask], kernel_size=kernel_size)
                oi['OI_VIS2'][k]['EV2'][i,mask] /= np.sqrt(kernel_size)

    if 'OI_T3' in oi.keys():
        for k in oi['OI_T3'].keys():
            for i in range(len(oi['OI_T3'][k]['MJD'])):
                mask = ~oi['OI_T3'][k]['FLAG'][i,:]
                oi['OI_T3'][k]['T3PHI'][i,mask] = scipy.signal.medfilt(
                    oi['OI_T3'][k]['T3PHI'][i,mask], kernel_size=kernel_size)
                oi['OI_T3'][k]['ET3PHI'][i,mask] /= np.sqrt(kernel_size)

                oi['OI_T3'][k]['T3AMP'][i,mask] = scipy.signal.medfilt(
                    oi['OI_T3'][k]['T3AMP'][i,mask], kernel_size=kernel_size)
                oi['OI_T3'][k]['ET3AMP'][i,mask] /= np.sqrt(kernel_size)
    return oi

def n_JHK(wl, T=None, P=None, H=None):
    """
    wl: wavelength in microns (only valid from 1.3 to 2.5um)
    T: temperature in K
    P: pressure in mbar
    H: relative humidity in %

    from https://arxiv.org/pdf/physics/0610256.pdf
    """
    nu = 1e4/wl
    nuref = 1e4/2.25 # cm−1

    # -- https://arxiv.org/pdf/physics/0610256.pdf
    # -- table 1
    # -- i; ciref / cmi; ciT / cmiK;  ciTT / [cmiK2]; ciH / [cmi/%]; ciHH / [cmi/%2]
    table1a=[[0, 0.200192e-3, 0.588625e-1, -3.01513, -0.103945e-7, 0.573256e-12],
             [1, 0.113474e-9, -0.385766e-7, 0.406167e-3, 0.136858e-11, 0.186367e-16],
             [2, -0.424595e-14, 0.888019e-10, -0.514544e-6, -0.171039e-14, -0.228150e-19],
             [3, 0.100957e-16, -0.567650e-13, 0.343161e-9, 0.112908e-17, 0.150947e-22],
             [4, -0.293315e-20, 0.166615e-16, -0.101189e-12, -0.329925e-21, -0.441214e-26],
             [5, 0.307228e-24, -0.174845e-20, 0.106749e-16, 0.344747e-25, 0.461209e-30]]

    # -- cip / [cmi/Pa]; cipp / [cmi/Pa2]; ciTH / [cmiK/%]; ciTp / [cmiK/Pa]; ciHp / [cmi/(% Pa)]
    table1b = [[0, 0.267085e-8, 0.609186e-17, 0.497859e-4, 0.779176e-6, -0.206567e-15],
               [1, 0.135941e-14, 0.519024e-23, -0.661752e-8, 0.396499e-12, 0.106141e-20],
               [2, 0.135295e-18, -0.419477e-27, 0.832034e-11, 0.395114e-16, -0.149982e-23],
               [3, 0.818218e-23, 0.434120e-30, -0.551793e-14, 0.233587e-20, 0.984046e-27],
               [4, -0.222957e-26, -0.122445e-33, 0.161899e-17, -0.636441e-24, -0.288266e-30],
               [5, 0.249964e-30, 0.134816e-37, -0.169901e-21, 0.716868e-28, 0.299105e-34]]

    Tref, Href, pref = 273.15+17.5, 10., 75e3
    if T is None:
        T = Tref
    if P is None:
        P = pref/100
    if H is None:
        H = Href

    n = 0.0
    p = P*100 # formula in Pa, not mbar

    for k,ca in enumerate(table1a):
        i = ca[0]
        ciref = ca[1]
        ciT = ca[2]
        ciTT = ca[3]
        ciH = ca[4]
        ciHH = ca[5]
        cb = table1b[k]
        cip = cb[1]
        cipp = cb[2]
        ciTH = cb[3]
        ciTp = cb[4]
        ciHp = cb[5]
        # -- equation 7
        ci = ciref + ciT*(1/T - 1/Tref) + ciTT*(1/T - 1/Tref)**2 +\
            ciH*(H-Href) + ciHH*(H-Href)**2 + cip*(p-pref) + cipp*(p-pref)**2 +\
            ciTH*(1/T-1/Tref)*(H-Href) + ciTp*(1/T-1/Tref)*(p-pref) +\
            ciHp*(H-Href)*(p-pref)
        # -- equation 6
        #print('mathar:', i, ciref, ci)
        n += ci*(nu - nuref)**i
    return n+1.0

def OI2FITS(oi, fitsfile):
    pass
