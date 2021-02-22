#!/usr/bin/env python3
# -*- coding: utf-8 -*-
'''
pyplr.oceanops
==============

A module to help with measurents for Ocean Optics spectrometers. 

'''

from time import sleep

import numpy as np
import pandas as pd
import spectres

# TODO: possibly sublclass Seabreeze functionality

def oo_measurement(spectrometer, integration_time=None, setting={}):
    '''
    For a given light source, use an adaptive procedure to find the integration 
    time which returns a spectrum whose maximum reported value in raw units is
    between 80-90% of the maximum intensity value for the device. Can take up
    to a maximum of ~3.5 mins for lower light levels, though this could be 
    reduced somewhat by optimising the algorithm.

    Parameters
    ----------
    spectrometer : seabreeze.spectrometers.Spectrometer
        The Ocean Optics spectrometer instance.
    integration_time : int
        The integration time to use for the measurement. Leave as None to
        adaptively set the integration time based on spectral measurements.
    setting : dict, optional
         The current setting of the light source (if known), to be included in
         the info_dict. For example {'led' : 5, 'intensity' : 3000}, or 
         {'intensities' : [0, 0, 0, 300, 4000, 200, 0, 0, 0, 0]}. 
         The default is {}.

    Returns
    -------
    oo_spectra : pd.DataFrame
        The resulting measurements from the Ocean Optics spectrometer.
    oo_info_dict : dict
        The companion info to the oo_spectra, with matching indices.

    '''
    
    if integration_time:
        # set the spectrometer integration time
        intgt = integration_time
        spectrometer.integration_time_micros(integration_time)
        sleep(.05)
        
        # obtain temperature measurements
        temps = spectrometer.f.temperature.temperature_get_all()
        sleep(.05)
        
        # obtain intensity measurements
        oo_counts = spectrometer.intensities()
        
        # get the maximum reported value
        max_reported = max(oo_counts)
        print('\tIntegration time: {} ms --> maximum value: {}'.format(
            integration_time / 1000, max_reported))
            
    else:    
        # initial parameters
        intgtlims = spectrometer.integration_time_micros_limits
        maximum_intensity = spectrometer.max_intensity
        lower_intgt = None
        upper_intgt = None
        lower_bound = maximum_intensity * .8
        upper_bound = maximum_intensity * .9
        
        # start with 1000 micros
        intgt = 1000.0 
        max_reported = 0
        
        # keep sampling with different integration times until the maximum reported
        # value is within 80-90% of the maximum intensity value for the device
        while max_reported < lower_bound or max_reported > upper_bound:
            
            # save a couple of mins on dark measurements
            # if setting['intensity'] == 0:
            #     intgt = intgtlims[1]
                
            # if the current integration time is greater than the upper 
            # limit, set it too the upper limit
            if intgt >= intgtlims[1]:
                intgt = intgtlims[1]
                
            # set the spectrometer integration time
            spectrometer.integration_time_micros(intgt)
            sleep(.05)
            
            # obtain temperature measurements
            temps = spectrometer.f.temperature.temperature_get_all()
            sleep(.05)
            
            # obtain intensity measurements
            oo_counts = spectrometer.intensities()
            
            # get the maximum reported value
            max_reported = max(oo_counts)
            print('\tIntegration time: {} ms --> maximum value: {}'.format(
                intgt / 1000, max_reported))
            
            # if the integration time has reached the upper limit for the spectrometer,
            # exit the while loop, having obtained the final measurement
            if intgt == intgtlims[1]:
                break
            
            # if the max_reported value is less than the lower_bound and the
            # upper_ingt is not yet known, update the lower_intgt and double intgt
            # ready for the next iteration
            elif max_reported < lower_bound and upper_intgt is None:
                lower_intgt = intgt
                intgt *= 2.0
            
            # if the max_reported value is greater than the upper_bound, update
            # the upper_intgt and subtract half of the difference between 
            # upper_intgt and lower_intgt from intgt ready for the next iteration
            elif max_reported > upper_bound:
                upper_intgt = intgt
                intgt -= (upper_intgt - lower_intgt) / 2 
                
            # if the max_reported value is less than the lower_bound and the value
            # of upper_intgt is known, update the lower_intgt and add half
            # of the difference between upper_intgt and lower_intgt to intgt ready 
            # for the next iteration
            elif max_reported < lower_bound and upper_intgt is not None:
                lower_intgt = intgt
                intgt += (upper_intgt - lower_intgt) / 2
    
    oo_info_dict = {
        'board_temp': temps[0],
        'micro_temp': temps[2],
        'integration_time': intgt,
        'model': spectrometer.model
        }
    oo_info_dict = {**oo_info_dict, **setting}
    
    # return the final counts and dict of sample-related info
    return oo_counts, oo_info_dict
    
def dark_measurement(spectrometer, integration_times=[1000]):
    '''
    Sample the dark spectrum with a range of integration times. Do this for a 
    range of temperatures to characterise the relationship between temperature
    and integration time.

    '''
    wls = spectrometer.wavelengths()
    data = pd.DataFrame()
    for intgt in integration_times:
        spectrometer.integration_time_micros(intgt)
        sleep(.05)
        temps = spectrometer.f.temperature.temperature_get_all()
        sleep(.05)
        board_temp = np.round(temps[0], decimals=2)
        micro_temp = np.round(temps[2], decimals=2)
        print('Board temp: {}, integration time: {}'.format(board_temp, intgt))
        intensities = pd.DataFrame(spectrometer.intensities())
        intensities.rename(columns={0:'dark_counts'}, inplace=True)
        data = pd.concat([data, intensities])
        
    midx = pd.MultiIndex.from_product(
        [[board_temp], [micro_temp], integration_times, wls],
        names=['board_temp', 'micro_temp', 'integration_time', 'wavelengths'])
    data.index = midx
    
    return data

def predict_dark_counts(spectra_info, darkcal):
    '''
    Predict the dark counts from the temperature and integration times of a
    set of measurements. These must be subtracted from measured pixel counts 
    during the unit-calibration process. 

    Parameters
    ----------
    spectra_info : pd.DataFrame
        The info dataframe containing the 'board_temp' and 'integration_time'
        variables.
    calfile : string
        Path to the calibration file. This is currenly generated in MATLAB. 

    Returns
    -------
    pd.DataFrame
        The predicted dark spectra.

    '''
    dark_counts = []
    
    for idx, row in spectra_info.iterrows():
        x  = spectra_info.loc[idx, 'board_temp']
        y  = spectra_info.loc[idx, 'integration_time']
        dark_spec = []
        
        for i in range(0, darkcal.shape[0]):
            p00 = darkcal.loc[i, 'p00']
            p10 = darkcal.loc[i, 'p10']
            p01 = darkcal.loc[i, 'p01']
            p20 = darkcal.loc[i, 'p20']
            p11 = darkcal.loc[i, 'p11']
            p30 = darkcal.loc[i, 'p30']
            p21 = darkcal.loc[i, 'p21']
            
            dark_spec.append(p00 
                             + p10*x 
                             + p01*y 
                             + p20*x*x 
                             + p11*x*y 
                             + p30*x*x*x 
                             + p21*x*x*y)

        dark_counts.append(dark_spec)
        
    # TODO: add code with function parameter to exclude poorly fitting pixels. 
    # using a visually determined threshold, for now. 
    FIT_RMSE_THRESHOLD = 110
    dark_counts = np.where(
        darkcal.rmse > FIT_RMSE_THRESHOLD, np.nan, dark_counts)
        
    return pd.DataFrame(dark_counts)

def calibrated_radiance(spectra, 
                        spectra_info, 
                        dark_spectra, 
                        cal_per_wl, 
                        sensor_area):
    
    # we have no saturated spectra due to adaptive measurement
    
    # convert integration time from us to s
    spectra_info['integration_time'] = (spectra_info['integration_time']
                                        / (1000*1000))
    
    cal_per_wl.index = spectra.columns
    dark_spectra.columns = spectra.columns
    uj_per_pixel = (spectra - dark_spectra) * cal_per_wl.T.values[0]
    wls = uj_per_pixel.columns.to_numpy(dtype='float')
    nm_per_pixel = np.hstack(
        [(wls[1]-wls[0]), (wls[2:]-wls[:-2])/2, (wls[-1]-wls[-2])])
    uj_per_nm = uj_per_pixel / nm_per_pixel
    uj_per_cm2_per_nm = uj_per_nm / sensor_area.loc[0, 0]
    uw_per_cm2_per_nm = uj_per_cm2_per_nm.div(
        spectra_info['integration_time'], axis='rows')
    
    # # Resample
    uw_per_cm2_per_nm = spectres.spectres(
        np.arange(380, 781), spectra.columns.to_numpy(
            dtype='float'), uw_per_cm2_per_nm.to_numpy())
    uw_per_cm2_per_nm = np.where(uw_per_cm2_per_nm < 0, 0, uw_per_cm2_per_nm)
    w_per_m2_per_nm = pd.DataFrame(uw_per_cm2_per_nm * 0.01)
    
    return w_per_m2_per_nm