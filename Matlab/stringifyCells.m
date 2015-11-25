function c=stringifyCells(c) 
%make all cell entries strings

nr=size(c,1);
nc=size(c,2);

for i=1:nr
    for j=1:nc
        if ~ischar(c{i,j})
            if isnumeric(c{i,j})
                c{i,j}=num2str(c{i,j});
            elseif isstruct(c{i,j})
                c{i,j}='nested data, can''t represent here';
            elseif iscell(c{i,j})
                c{i,j}='nested data, can''t represent here';
            end
        end
    end
end
