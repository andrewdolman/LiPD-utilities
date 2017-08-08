function C=readLiPDChronData1_3(I,dirname)


toct=NaN;
if isfield(I,'chronData')
    C=I.chronData;
    toct=1:length(I.chronData);
end



if ~isnan(toct) %if there are chron tables, load em in
    for i = toct; %go through each chronology
        %%%%%CHRON MEASUREMENT TABLE
        
        if isfield(I.chronData{i},'measurementTable')
            %Go through measurementTable first
            for cmt=1:length(I.chronData{i}.measurementTable)
                cT = I.chronData{i}.measurementTable{cmt};
                
                cT=readLiPDTable(cT,dirname);
                cT=processLiPDColumns(cT);
                if size(I.chronMeasMD5,1)>=i
                    if iscell(I.chronMeasMD5)
                        cT.chronMeasMD5 = I.chronMeasMD5{i,2};
                    end
                end
                C{i}.measurementTable{cmt}=cT;
            end
        end
        
        %%%%%%END CHRON MEASUREMENT TABLE
        
        
        
        %%%%%%%START CHRON MODELS
        if isfield(I.chronData{i},'model')
            for cm=1:length(I.chronData{i}.model)
                CMS = I.chronData{i}.model{cm};
                
                %%%%% CHRON MODEL TABLE
                if isfield(CMS,'summaryTable')
                    CMT=readLiPDTable(CMS.summaryTable,dirname);
                    CMT=processLiPDColumns(CMT);
                    if size(I.chronSummaryTableMD5,1)>=i
                        CMT.summaryTableMD5=I.chronSummaryTableMD5{i,2};
                    end
                    
                    C{i}.model{cm}.summaryTable=cT;
                end
                
                %%%%% CHRON ENS TABLE
                if isfield(CMS,'ensembleTable')
                    CME=readLiPDTable(CMS.ensembleTable,dirname);
                    CME=processLiPDColumns(CME);
                    if size(I.chronEnsMD5,1)>=i
                        CME.chronEnsembleMD5=I.chronEnsMD5{i,2};
                    end
                    C{i}.model{cm}.ensembleTable=CME;
                    
                end
                
                %%%%% CALIBRATED AGE DATA
                if isfield(CMS,'distributionTable')
                    C{i}.model{cm}.distributionTable=cell(1,1);
                    for ca = 1:length(CMS.distributionTable)
                        CASA=readLiPDTable(CMS.distributionTable{ca},dirname);
                        CASA=processLiPDColumns(CASA);
                        C{i}.model{cm}.distributionTable(ca)={CASA};
                    end
                end
                
            end
        end
    end
else
    C=NaN;
    
end